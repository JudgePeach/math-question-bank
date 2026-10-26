"""Bounded, read-only candidate and exam-reference planning for paper selection.

Source labels are local evidence, not independent verification of an official
paper. These helpers never mutate questions or infer numeric difficulty from
MathBank's teaching-purpose labels.
"""

from collections import Counter
import hashlib
import json
import re

from sqlalchemy import and_, func, or_

from mathbank.database import KnowledgePoint, Question, QuestionKnowledgePoint
from mathbank.source_comments import strip_source_attribution_comment


MAX_CANDIDATES = 120
MAX_REFERENCE_ROWS = 6000
QUESTION_TYPES = ("single_choice", "multi_choice", "fill_in_blank", "detailed_answer")
_TOPIC_ALIASES = {
    "集合": ("集合",), "复数": ("复数",), "函数": ("函数",),
    "导数": ("导数", "导函数"), "数列": ("数列",),
    "三角函数": ("三角函数", "三角恒等"), "解三角形": ("解三角形", "正弦定理", "余弦定理"),
    "平面向量": ("平面向量", "向量"), "立体几何": ("立体几何", "空间几何", "空间向量"),
    "概率": ("概率",), "统计": ("统计",), "排列组合": ("排列", "组合", "计数原理"),
    "解析几何": ("解析几何", "圆锥曲线", "椭圆", "双曲线", "抛物线", "直线与圆"),
    "圆锥曲线": ("圆锥曲线", "椭圆", "双曲线", "抛物线"), "不等式": ("不等式",),
}
_TYPE_WORDS = {
    "single_choice": ("单选", "单项选择"), "multi_choice": ("多选", "多项选择"),
    "fill_in_blank": ("填空",), "detailed_answer": ("解答",),
}
_EXAM_NOISE = re.compile(r"模拟|一模|二模|三模|联考|适应|预测|改编|原创|讲义|专题|复习|选编|汇编|精选|链接|练习|月考|期中|期末|押题|备考|练考|训练")
_PROVINCES = "北京|天津|上海|浙江|江苏|山东|海南"
_EXAM_NAME = re.compile(
    rf"^(?:(?:高考真题|高考|数学)[:：·•,，、-]*)?"
    rf"(?P<name>(?:(?:全国|新课标|新高考)(?P<volume>[一二三123ⅠⅡⅢIVI]+)卷|(?:{_PROVINCES})(?:高考)?(?:数学)?卷?))"
    r"(?P<track>[（(]?[文理](?:科)?[）)]?)?(?:数学)?(?:高考真题|高考|真题)?$"
)


def _positive_integer(value, field, *, maximum=30):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field}须为1至{maximum}的整数。")
    return value


def _cn_number(value):
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.count("十") == 1:
        left, right = value.split("十")
        if (not left or left in digits) and (not right or right in digits):
            return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value)


def _negated_at(prompt, start):
    """Recognize only explicit local exclusion, with a small list scope.

    Arbitrary text between the exclusion verb and target is not interpreted;
    e.g. 不选题型为解答题 is deliberately left to the model/teacher.
    """
    prefix = prompt[max(0, start - 24):start]
    prior_targets = [word + "题?" for words in _TYPE_WORDS.values() for word in words]
    prior_targets += ["选择题", "旧题", "重做", "错题", "做过的?", "考过的?", "已抽过的?", "已用过的?"]
    prior_targets += list(_TOPIC_ALIASES)
    targets = "|".join(prior_targets)
    tail = rf"(?:\s|再|已经|曾经|曾|已|任何|所有|的|(?:{targets})|[、和与及或])*"
    return bool(re.search(rf"(?:不要|不选|排除|不含|不考){tail}$", prefix))


def _question_type_intents(prompt):
    positive, negative = set(), set()
    spans = []
    for kind, words in _TYPE_WORDS.items():
        for word in words:
            for match in re.finditer(re.escape(word), prompt):
                spans.append((match.start(), match.end()))
                (negative if _negated_at(prompt, match.start()) else positive).add(kind)
    for match in re.finditer("选择题", prompt):
        # 单项选择题/多项选择题 already have a precise type.
        if any(left <= match.start() < right for left, right in spans):
            continue
        (negative if _negated_at(prompt, match.start()) else positive).update(("single_choice", "multi_choice"))
    return ([kind for kind in QUESTION_TYPES if kind in positive - negative],
            [kind for kind in QUESTION_TYPES if kind in negative])


def parse_question_types(prompt):
    """Infer a single positive type; mixed requests remain mixed."""
    positive, _negative = _question_type_intents(prompt)
    return positive[0] if len(positive) == 1 else ""


def extract_selection_topics(prompt):
    topics = []
    for name in _TOPIC_ALIASES:
        for match in re.finditer(re.escape(name), prompt):
            # An occurrence inside 三角函数 does not request general functions.
            # An independent 函数 occurrence in the same prompt still counts.
            if name == "函数" and prompt[max(0, match.start()-2):match.start()] == "三角":
                continue
            if _negated_at(prompt, match.start()):
                continue
            topics.append(name)
            break
    return topics


def parse_selection_request(payload):
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 4000:
        raise ValueError("组卷要求须为不超过4000字的文字。")
    limit = _positive_integer(payload.get("limit", 5), "题数")
    # Only an overall count is inferred. Per-type numbers do not redefine it.
    matches = [match for match in re.finditer(r"(?<![第零一二两三四五六七八九十\d])([零一二两三四五六七八九十\d]+)\s*(?:道|题)(?![型目])", prompt)
               if not _negated_at(prompt, match.start())]
    overall = next((match for match in matches if re.search(
        r"(?:总计|总共|合计|一共|共|抽取|选取|挑选|组(?:一份|一套)?|出(?:一份|一套)?|选)\s*$",
        prompt[max(0, match.start()-8):match.start()])), None)
    count_match = overall or (matches[0] if len(matches) == 1 else None)
    if count_match is not None:
        inferred = _cn_number(count_match.group(1))
        if inferred is not None:
            limit = _positive_integer(inferred, "要求中的题数")
    counts = payload.get("type_counts") or {}
    if not isinstance(counts, dict) or len(counts) > 12:
        raise ValueError("题型配额须为题型和非负整数的映射。")
    if not counts:
        inferred_counts = {}
        for kind, words in _TYPE_WORDS.items():
            choices = "|".join(re.escape(word) for word in words)
            match = re.search(rf"([零一二两三四五六七八九十\d]+)\s*(?:道|题)?\s*(?:{choices})(?:题)?", prompt)
            if match and not _negated_at(prompt, match.start()):
                count = _cn_number(match[1])
                if count is not None:
                    inferred_counts[kind] = count
        # A single scoped count (5道函数单选题) remains the ordinary count.
        if len(inferred_counts) > 1:
            counts = inferred_counts
    normalized_counts = {}
    for kind, count in counts.items():
        if not isinstance(kind, str) or not kind or len(kind) > 50 or type(count) is not int or not 0 <= count <= 30:
            raise ValueError("题型配额须为题型和0至30的整数。")
        if count:
            normalized_counts[kind] = count
    if normalized_counts:
        total = sum(normalized_counts.values())
        _positive_integer(total, "配额总题数")
        limit = total
    excluded = payload.get("exclude_ids") or []
    if not isinstance(excluded, list) or len(excluded) > 2000 or any(type(qid) is not int or qid <= 0 for qid in excluded):
        raise ValueError("已选题目ID须为至多2000个正整数。")
    positive_types, excluded_types = _question_type_intents(prompt)
    single_type = payload.get("question_type") or (positive_types[0] if len(positive_types) == 1 else "")
    review = any(not _negated_at(prompt, match.start()) for match in re.finditer(
        r"做过|考过|已抽过|已用过|用过的|旧题|重做|错题|以往用题", prompt))
    return {
        "limit": limit,
        "question_type": single_type,
        "question_types": positive_types if not single_type and not normalized_counts else [],
        "excluded_question_types": excluded_types,
        "review": review,
        "topics": extract_selection_topics(prompt),
        "type_counts": normalized_counts,
        "excluded_ids": list(dict.fromkeys(excluded)),
    }


def parse_gaokao_source(source, current_year=None):
    """Recognize compact whole-paper labels; never mine a lecture's citations."""
    if not isinstance(source, str) or not source.strip() or len(source) > 100 or _EXAM_NOISE.search(source):
        return None
    text = re.sub(r"\s+", "", source).strip("（()）[]【】")
    year_match = re.match(r"^(20\d{2})(?:年)?[·•,，、:：-]*", text)
    if not year_match:
        return None
    year = int(year_match[1])
    if current_year is not None and year > current_year:
        return None
    rest = text[year_match.end():]
    number = None
    number_match = re.search(r"[·•,，、:：-]*(?:第)?([1-9]\d?)(?:题|[.．])?$", rest)
    if number_match:
        number = int(number_match[1])
        rest = rest[:number_match.start()]
    rest = rest.strip("·•,，、:：-()（）")
    name_match = _EXAM_NAME.fullmatch(rest)
    if not name_match:
        return None
    name = name_match["name"]
    volume = name_match["volume"]
    if volume:
        vol = {"一": "1", "二": "2", "三": "3", "Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "I": "1", "II": "2", "III": "3"}.get(volume, volume)
        if vol not in {"1", "2", "3"}:
            return None
        series = "national_" + vol
    else:
        series = re.sub(r"高考|数学|卷", "", name)
    track = name_match["track"] or ""
    if track:
        series += "_" + ("文" if "文" in track else "理")
    return {"key": f"{year}:{series}", "series": series, "title": f"{year}年{name}{track}",
            "year": year, "number": number}


def _reference_profiles(db):
    # Only compact scalar metadata is scanned, with a defensive upper bound.
    rows = (db.query(Question.id, Question.source, Question.question_type, Question.difficulty)
            .filter(Question.source.like("20%"), Question.source.contains("卷"), func.length(Question.source) <= 100)
            .order_by(Question.source.desc(), Question.id.desc()).limit(MAX_REFERENCE_ROWS).all())
    groups = {}
    for row in rows:
        source = parse_gaokao_source(row.source)
        if source is None:
            continue
        group = groups.setdefault(source["key"], {**source, "rows": [], "numbers": set(), "duplicates": False})
        if source["number"] is not None:
            if source["number"] in group["numbers"]:
                group["duplicates"] = True
                continue
            group["numbers"].add(source["number"])
        group["rows"].append((row.id, row.question_type, row.difficulty, source["number"]))
    years = sorted({item["year"] for item in groups.values()}, reverse=True)[:3]
    profiles = []
    for item in groups.values():
        if item["year"] not in years:
            continue
        numbers = item["numbers"]
        # Broad source labels on scattered exercises must not pollute a numbered
        # paper imported with precise per-question provenance.
        if numbers:
            item["rows"] = [row for row in item["rows"] if row[3] is not None]
        largest = max(numbers, default=0)
        complete = (largest in {19, 22, 23} and numbers == set(range(1, largest+1))
                    and len(item["rows"]) == largest and not item["duplicates"])
        item["_ids"] = [row[0] for row in sorted(item["rows"], key=lambda row: (row[3] or 100, row[0]))]
        item["question_count"] = len(item["rows"])
        item["type_counts"] = dict(Counter(row[1] for row in item["rows"]))
        item["difficulty_labels"] = dict(Counter(row[2] or "未标注" for row in item["rows"]))
        item["complete"] = complete
        item["completeness_note"] = ("库内题号连续且数量符合常见整卷；来源未经外部核验。" if complete
            else "库内仅有部分题目或题号不完整，题型比例仅供观察，不作为整卷配额。")
        item["main_knowledge_counts"] = []
        profiles.append(item)
    profiles.sort(key=lambda item: (-item["year"], not item["complete"], item["series"]))
    profiles = profiles[:24]
    ids = [qid for item in profiles for qid in item["_ids"]]
    knowledge_by_id = {}
    if ids:
        labels = (db.query(QuestionKnowledgePoint.question_id, KnowledgePoint.code, KnowledgePoint.name)
                  .join(KnowledgePoint, KnowledgePoint.code == QuestionKnowledgePoint.point_code)
                  .filter(QuestionKnowledgePoint.question_id.in_(ids), QuestionKnowledgePoint.role == "main").all())
        for qid, code, name in labels:
            knowledge_by_id.setdefault(qid, []).append((code, name))
    for item in profiles:
        by_id = {row[0]: row for row in item["rows"]}
        item["slots"] = [
            {"number": by_id[qid][3], "question_type": by_id[qid][1],
             "difficulty_label": by_id[qid][2] or "未标注",
             "main_knowledge_points": [{"code": code, "name": name}
                                        for code, name in knowledge_by_id.get(qid, [])[:12]]}
            for qid in item["_ids"]
        ]
        points = Counter(point for qid in item["_ids"] for point in knowledge_by_id.get(qid, []))
        item["main_knowledge_counts"] = [{"code": code, "name": name, "count": count}
                                         for (code, name), count in points.most_common(20)]
    return profiles


def _public_profile(item):
    keys = ("key", "series", "title", "year", "question_count", "type_counts", "difficulty_labels",
            "complete", "completeness_note", "main_knowledge_counts", "slots")
    return {key: item[key] for key in keys}


def list_reference_exams(db):
    return [_public_profile(item) for item in _reference_profiles(db)]


def build_reference_context(db, reference_key=None):
    if reference_key == "none":
        return {"enabled": False, "reference_key": "none", "references": [], "warnings": []}
    if reference_key is not None and (not isinstance(reference_key, str) or len(reference_key) > 100):
        raise ValueError("参考卷标识无效。")
    profiles = _reference_profiles(db)
    if reference_key not in {None, "", "auto"}:
        selected = next((item for item in profiles if item["key"] == reference_key), None)
        if selected is None:
            raise ValueError("参考卷已不存在或不在最近三年收录范围内，请刷新参考卷列表。")
    else:
        selected = next((item for item in profiles if item["complete"]), profiles[0] if profiles else None)
    if selected is None:
        return {"enabled": True, "reference_key": "auto", "references": [],
                "warnings": ["题库中未找到可识别的近年高考卷来源，本次仅按教师要求选题。"]}
    related = [selected]
    if selected["complete"]:
        related += [item for item in profiles if item["complete"] and item["series"] == selected["series"]
                    and item["type_counts"] == selected["type_counts"] and item["year"] < selected["year"]][:2]
    context = []
    for item in related:
        # Spread at most five examples across the paper, including its last task.
        ids = item["_ids"]
        sample_ids = list(dict.fromkeys(ids[round(index * (len(ids)-1)/4)] for index in range(5))) if ids else []
        rows = (db.query(Question.id, Question.content, Question.question_type, Question.source)
                .filter(Question.id.in_(sample_ids)).all())
        by_id = {row.id: row for row in rows}
        profile = _public_profile(item)
        profile["samples"] = [{"id": qid, "question_type": by_id[qid].question_type,
                               "source": by_id[qid].source,
                               "stem_excerpt": strip_source_attribution_comment(by_id[qid].content or "")[:1200],
                               "excerpt_truncated": len(strip_source_attribution_comment(by_id[qid].content or "")) > 1200}
                              for qid in sample_ids if qid in by_id]
        context.append(profile)
    warnings = []
    if any(not item["complete"] for item in related):
        warnings.append("部分参考卷收录不完整，只参考可见设问与考点，不套用其题型比例。")
    return {"enabled": True, "reference_key": selected["key"], "references": context, "warnings": warnings}


def _topic_terms(topics):
    return list(dict.fromkeys(term for topic in topics for term in _TOPIC_ALIASES.get(topic, (topic,))))


def _topic_filter(topics):
    terms = _topic_terms(topics)
    conditions = []
    for term in terms:
        conditions.extend(column.contains(term, autoescape=True) for column in (
            Question.category_chapter, Question.category_knowledge, Question.tags, Question.content))
        conditions.append(Question.knowledge_points.any(and_(
            QuestionKnowledgePoint.role == "main", QuestionKnowledgePoint.point.has(KnowledgePoint.name.contains(term, autoescape=True)))))
    return or_(*conditions) if conditions else None


def collect_candidates(query, limit=5, excluded_ids=(), topics=(), review=False, type_counts=None):
    """SQL filters precede bounded, stratified hydration; never scan full ORM rows."""
    if excluded_ids:
        query = query.filter(~Question.id.in_(list(excluded_ids)))
    topic_filter = _topic_filter(topics)
    if topic_filter is not None:
        query = query.filter(topic_filter)
    usage = func.coalesce(Question.usage_count, 0)
    ordering = (usage.desc() if review else usage.asc(), Question.id.desc())
    kinds = list(type_counts or {})
    if not kinds:
        kinds = [row[0] for row in query.enable_eagerloads(False).with_entities(Question.question_type)
                 .distinct().order_by(Question.question_type).limit(12).all()]
    if not kinds:
        return []
    # Proportional budgets protect large requested sections even when many
    # custom types coexist. Largest remainders keep the sum exactly bounded.
    weights = {kind: max(1, (type_counts or {}).get(kind, 1)) for kind in kinds}
    weight_total = sum(weights.values())
    budgets = {kind: MAX_CANDIDATES * weights[kind] // weight_total for kind in kinds}
    remainder = MAX_CANDIDATES - sum(budgets.values())
    ranked_remainders = sorted(kinds, key=lambda kind: -(MAX_CANDIDATES * weights[kind] % weight_total))
    for kind in ranked_remainders[:remainder]:
        budgets[kind] += 1
    records, seen = [], set()
    for kind in kinds:
        for question in query.filter(Question.question_type == kind).order_by(*ordering).limit(budgets[kind]).all():
            if question.id not in seen:
                records.append(question)
                seen.add(question.id)
    remaining = MAX_CANDIDATES - len(records)
    if remaining > 0:
        supplement = query
        if seen:
            supplement = supplement.filter(~Question.id.in_(seen))
        if type_counts:
            supplement = supplement.filter(Question.question_type.in_(kinds))
        records.extend(supplement.order_by(*ordering).limit(remaining).all())
    return records[:MAX_CANDIDATES]


def _content_key(question):
    content = strip_source_attribution_comment(question.content or "")
    # Legacy questions may keep their only diagram outside the Markdown stem.
    # Such figures distinguish problems with otherwise identical wording.
    assets = {"images": getattr(question, "image_paths", []) or [],
              "tikz": getattr(question, "tikz_code", "") or ""}
    material = re.sub(r"\s+", "", content) + json.dumps(assets, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def select_local_candidates(candidates, limit, topics=(), review=False, type_counts=None):
    """Conservative deterministic fallback with content de-duplication."""
    terms = _topic_terms(topics)
    def rank(question):
        points = " ".join(row.point.name for row in getattr(question, "knowledge_points", ()) if row.role == "main" and row.point)
        metadata = " ".join(str(getattr(question, field, "") or "") for field in ("category_chapter", "category_knowledge", "tags"))
        relevance = sum(5 * (term in points) + 3 * (term in metadata) + (term in (question.content or "")) for term in terms)
        usage = question.usage_count or 0
        return (-relevance, -usage if review else usage, -question.id)
    ordered = sorted(candidates, key=rank)
    selected, seen, type_used = [], set(), Counter()
    # Distinct main targets break ties in local mode; they are not invented labels.
    covered = set()
    while ordered and len(selected) < limit:
        eligible = [question for question in ordered if not type_counts or type_used[question.question_type] < type_counts.get(question.question_type, 0)]
        if not eligible:
            break
        best_rank = rank(eligible[0])[0]
        tied = [question for question in eligible if rank(question)[0] == best_rank]
        def new_points(question):
            return len({row.point_code for row in getattr(question, "knowledge_points", ()) if row.role == "main"} - covered)
        question = max(tied, key=new_points)
        ordered.remove(question)
        key = _content_key(question)
        if key in seen:
            continue
        seen.add(key)
        selected.append(question)
        type_used[question.question_type] += 1
        covered.update(row.point_code for row in getattr(question, "knowledge_points", ()) if row.role == "main")
    return selected
