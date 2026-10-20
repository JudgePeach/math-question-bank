# -*- coding: utf-8 -*-
"""PDF 本地切块裁判（纯本地、零网络、零 AI、可离线单测）。

把整卷源文按**行首题号**切成「干净题块」+「可疑题块」，并给出整卷级裁决 trustworthy。
当前仅作为可选诊断接入（MATHBANK_PDF_LOCAL_SPLIT=1），不改变原有 AI 拆题流程。

本模块**顶层绝不 import main / parsing_pipeline**（那会触发 main 抢 runtime 锁，
离线脚本无法运行）。所需的纯原语一律在本文件内镜像实现；确需复用的
`pdf_inspector_helper` 亦不 import main，可安全加载。

可信裁决是保守的：漏题号、疑似并题、分节声明题数不符、共享条件/跨题引用、
首题前含公式或配图等任一证据成立，整卷 trustworthy=False，交回原全文拆题路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

try:  # 公式流失特征检测（pdf_path=None 时是纯正则，无重依赖）
    from mathbank.pdf_inspector_helper import _has_math_formula_loss as _loss_probe
except Exception:  # pragma: no cover - 极端环境降级为不调用
    _loss_probe = None


# ============================ 镜像的纯原语 ============================
_DIGITS = "0123456789０１２３４５６７８９"

# MinerU 常把题号排成标题/加粗（`# 6．`、`**6.**`、`\textbf{6.}`），也会输出全角数字与 `6\.`。
# 半角 `.` 紧跟数字视为小数（`3.5`）直接拒绝；全角点紧跟数字（`6．5名同学`）在
# `_find_anchors` 中仅当它正是下一个期望题号时才接受。
_QUESTION_NO_RE = re.compile(
    r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*[ \t]*|\\textbf\{[ \t]*)?"
    r"(?P<num>[0-9０-９]{1,3})(?:[ \t]*(?:\*\*|\}))?[ \t]*"
    r"(?P<punct>\\?\.(?![0-9０-９])|[．、])"
    r"(?:[ \t]*(?:\*\*|\}))?[ \t]*"
)
_PAGE_MARKER_RE = re.compile(r"[ \t]*<!--\s*MATHBANK_PDF_PAGE:\s*\d+\s*-->[ \t]*\n?", re.IGNORECASE)
_SOLUTION_MARK_RE = re.compile(
    r"【答案】|【详解】|【解析】|【参考详解】|参考答案|答案与解析|答案解析|试题详解"
)
_ANSWER_HEAD_RE = re.compile(
    r"【答案】|【解析】|【详解】|参考答案|答案与解析|答案解析|试题详解"
    r"|(?:^|\n)[ \t]*(?:答案|解析|详解)[：:]"
)
_SOLUTION_MARKER_RE = re.compile(r"【详解】|【解析】|详解[：:]|解析[：:]")
_OPTION_LABEL_LINE_RE = re.compile(r"(?m)^[ \t]*([A-H])[ \t]*[.．、]?[ \t]*(?=\S|$)")
_INLINE_LABEL_RE = re.compile(r"[A-H][.．、]")
_FILLIN_HINT_RE = re.compile(r"\\fillin|_{2,}|__+")
# 卷首答案速查区/详解区标题（仅当出现在首个真 1. 锚点之前才用于跳过前奏）。
_ANSWER_ZONE_HEAD_RE = re.compile(r"【参考详解】|【参考答案】|参考答案|答案与解析|答案解析|试题详解")
_NOTICE_HEAD_RE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*(?:注意事项|考生须知|答题须知)")
# 选项标号（行首或行内），用于本地兜底判型；要求标号后紧跟内容或行尾/换行。
_OPTION_LABEL_ANY_RE = re.compile(r"(?:^|[\s（(])([A-H])[ \t]*[.．、](?=[ \t]*\S|[ \t]*(?:$|\n))")
_SECTION_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*(?:[一二三四五六七八九十]{1,3}\s*[、.．，,]\s*)?"
    r"(?:单项选择|多项选择|不定项选择|选择题|单选题|多选题|填空题|解答题|必考题?|选考题?|判断题|卷[ⅠⅡ])"
)
_MULTI_SECTION_RE = re.compile(
    r"多项选择|多选题|不定项|有多项符合|有多个选项符合|多个选项符合|至少有两(?:个|项)|两个或两个以上"
)
_SINGLE_SECTION_RE = re.compile(r"单项选择|单选题|只有一项|只有一个选项|只有一个是")
_MULTI_STEM_HINT_RE = re.compile(
    r"[（(【\[]\s*(?:多选|多项选择|不定项)\s*题?\s*[）)】\]]|有多项符合题目要求|有多个选项符合"
)
_ANSWER_LETTERS_RE = re.compile(
    r"(?:故选|应选|答案\s*(?:为|是)|【答案】|答案[：:])\s*[:：]?\s*\$?\s*"
    r"(?:\\(?:mathrm|text|rm|textbf)\s*\{\s*)?"
    r"([A-H](?:[ \t]*[,，、和与及]?[ \t]*[A-H])*)"
    r"(?![A-Za-z0-9_=^{(（])"
)
_DECLARED_COUNT_RE = re.compile(r"共\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*(?:个|道)?\s*小?题")
_SHARED_CONTEXT_RE = re.compile(
    r"(?:共用|公用)(?:题干|条件|材料|信息|图|数据)"
    r"|(?:回答|完成|解答)\s*(?:下面|下列|以下|下述|后面)?\s*的?\s*第?\s*[0-9０-９]{1,3}\s*"
    r"(?:[～~\-—–至到、,，和与]\s*[0-9０-９]{1,3}\s*)+小?题"
    r"|(?:回答|完成|解答)\s*(?:下面|下列|以下|下述|后面)\s*的?\s*(?:[两二三四五六几各]|[0-9])\s*(?:个|道)?\s*小?题"
    r"|第?\s*[0-9０-９]{1,3}\s*[～~\-—–至到]\s*[0-9０-９]{1,3}\s*小?题\s*(?:共用|公用|使用|基于|依据|根据)"
)
_CROSS_REF_RE = re.compile(r"第\s*([0-9０-９]{1,3})\s*小?题(?!图)|(?:上一?|前一?)小?题")
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_INCIMG_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# ============================ 阈值常量（D1/D2/G13）============================
MIN_PRESERVED_RATIO = 0.60   # D1
MAX_SUSPICIOUS_RATIO = 0.30  # D2
MAX_SUSPICIOUS_ABS = 8       # D2
MIN_TOTAL = 5                # G13
MAX_TOTAL = 60               # G13
_MIN_STEM_CHARS = 6          # G8


def _clean(text: str) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]", text or ""))


def _strip_hash_prefix(text: str) -> str:
    return re.sub(r"(?m)^[ \t]*#+[ \t]*", "", text or "")


def _image_refs(segment: str) -> List[str]:
    refs: List[str] = []
    for m in _MD_IMG_RE.finditer(segment or ""):
        u = m.group(1).strip()
        if u:
            refs.append(u)
    for m in _INCIMG_RE.finditer(segment or ""):
        p = m.group(1).strip()
        if p:
            refs.append(p)
    return refs


def _has_paired_dollar(text: str) -> bool:
    if (text or "").count("$") % 2 != 0:
        return False
    return len(re.findall(r"\\begin\{", text or "")) == len(re.findall(r"\\end\{", text or ""))


def _count_option_labels_loose(seg: str) -> int:
    labels = set()
    for m in _OPTION_LABEL_ANY_RE.finditer(_strip_hash_prefix(seg or "")):
        labels.add(m.group(1))
    return len(labels)


def _infer_question_type_local(segment: str) -> str:
    seg = _strip_hash_prefix(segment or "")
    if _count_option_labels_loose(seg) >= 2:
        return "single_choice"
    if _FILLIN_HINT_RE.search(seg):
        return "fill_in_blank"
    return "detailed_answer"


def _count_source_options(segment: str) -> int:
    cleaned = _strip_hash_prefix(segment)
    valid = set()
    for m in _OPTION_LABEL_LINE_RE.finditer(cleaned):
        line_end = cleaned.find("\n", m.start())
        if line_end == -1:
            line_end = len(cleaned)
        after = cleaned[m.end():line_end]
        if not _INLINE_LABEL_RE.search(after):
            valid.add(m.group(1))
    count = 0
    for c in "ABCDEFGH":
        if c in valid:
            count += 1
        else:
            break
    return count


def _answer_choice_letters(answer: str) -> set:
    """原版答案中单次声明的最大选项字母集合（`故选：AD`、`【答案】BC`）。"""
    best: set = set()
    for m in _ANSWER_LETTERS_RE.finditer(answer or ""):
        letters = set(re.findall(r"[A-H]", m.group(1)))
        if len(letters) > len(best):
            best = letters
    return best


def _parse_count(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十"):
        return 10 + _CN_NUM.get(token[1:], 0)
    if token.endswith("十"):
        return _CN_NUM.get(token[:-1], 0) * 10 or None
    if "十" in token:
        tens, ones = token.split("十", 1)
        return _CN_NUM.get(tens, 0) * 10 + _CN_NUM.get(ones, 0)
    return _CN_NUM.get(token)


def _section_kind(preamble: str) -> str:
    if _MULTI_SECTION_RE.search(preamble or ""):
        return "multi_choice"
    if _SINGLE_SECTION_RE.search(preamble or ""):
        return "single_choice"
    return ""


@dataclass
class Block:
    block_id: str
    paper_question_no: str
    start: int
    body_start: int
    end: int
    content: str = ""
    answer_markdown: str = ""
    referenced_images: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    local_type: str = ""
    forced_type: str = ""
    section_kind: str = ""

    def to_parsed_question(self, classification: Optional[dict]) -> dict:
        cls = classification or {}
        # 分节声明/原版多字母答案是来源证据，优先于模型返回的题型。
        q_type = self.forced_type or (cls.get("question_type") or "").strip() or self.local_type
        q: dict = {
            "content": self.content,
            "answer_markdown": self.answer_markdown,
            "question_type": q_type,
            "category_compulsory": cls.get("category_compulsory") or "",
            "category_chapter": cls.get("category_chapter") or "",
            "category_knowledge": cls.get("category_knowledge") or "",
            "topic": cls.get("topic") or "",
            "difficulty": cls.get("difficulty") or "",
            "source": cls.get("source") or "",
            "paper_question_no": str(cls.get("paper_question_no") or self.paper_question_no).strip(),
            "referenced_images": list(self.referenced_images),
        }
        if not (cls.get("question_type") or cls.get("category_chapter") or cls.get("difficulty")):
            q["review"] = "元数据待refine（快路径分类缺失，正文已本地原样保留）"
        return q


@dataclass
class SplitPlan:
    clean_blocks: List[Block] = field(default_factory=list)
    suspicious_blocks: List[Block] = field(default_factory=list)
    all_blocks: List[Block] = field(default_factory=list)
    cover_ok: bool = False
    trustworthy: bool = False
    judge_report: dict = field(default_factory=dict)


def _normalize_text(source_text: str) -> str:
    text = _PAGE_MARKER_RE.sub("", source_text or "")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _find_anchors(text: str) -> List[dict]:
    anchors: List[dict] = []
    for m in _QUESTION_NO_RE.finditer(text):
        num = int(m.group("num"))
        if not (1 <= num <= 999):
            continue
        punct_end = m.end("punct")
        glued = punct_end < len(text) and text[punct_end] in _DIGITS
        if glued and num != 1 and not (anchors and num == anchors[-1]["num"] + 1):
            continue
        anchors.append({"no": str(num), "num": num, "start": m.start(), "body_start": m.end()})
    for i, a in enumerate(anchors):
        a["end"] = anchors[i + 1]["start"] if i + 1 < len(anchors) else len(text)
    return anchors


def _skip_notice_zone(text: str, report: dict) -> str:
    m_notice = _NOTICE_HEAD_RE.search(text)
    if not m_notice:
        return text
    m_section = _SECTION_HEADER_RE.search(text, m_notice.end())
    if not m_section:
        return text
    report["notice_zone_skipped"] = True
    return text[:m_notice.start()] + text[m_section.start():]


def _inline_next_number(segment: str, num: int) -> bool:
    nxt = str(num + 1)
    wide = "".join(_DIGITS[10 + int(c)] for c in nxt)
    pattern = (
        r"(?<=[ \t\u3000])(?:\*\*)?(?:" + nxt + "|" + wide + r")(?:\*\*)?[ \t]*"
        r"(?:\.(?![0-9０-９])|．)(?:\*\*)?[ \t]*(?=[\u4e00-\u9fa5$\\(（!])"
    )
    return re.search(pattern, segment or "") is not None


def _block_gate_flags(block: Block, text: str) -> List[str]:
    flags: List[str] = []
    raw_seg = text[block.body_start:block.end]
    if _loss_probe is not None:
        try:
            if _loss_probe(raw_seg):
                flags.append("G5_公式流失")
        except Exception:
            flags.append("G5_探测异常")
    if not _has_paired_dollar(block.content) or not _has_paired_dollar(block.answer_markdown):
        flags.append("G6_定界符不配平")
    stem_clean = _clean(block.content)
    if len(stem_clean) < _MIN_STEM_CHARS and not block.referenced_images and "$" not in block.content:
        flags.append("G8_题干过空")
    return flags


def _resolve_choice_type(block: Block) -> Optional[str]:
    """据分节声明、题干提示与原版答案字母确定单/多选；冲突返回 None 并打标。"""
    has_options = _count_option_labels_loose(block.content) >= 2
    letters = _answer_choice_letters(block.answer_markdown)
    multi_by_answer = len(letters) >= 2
    multi_by_stem = bool(_MULTI_STEM_HINT_RE.search(block.content))
    if block.section_kind == "single_choice" and (multi_by_answer or multi_by_stem):
        block.risk_flags.append("G10_题型冲突")
        return None
    if multi_by_answer or multi_by_stem or (block.section_kind == "multi_choice" and has_options):
        return "multi_choice"
    if block.section_kind == "single_choice" and has_options:
        return "single_choice"
    return None


def plan_pdf_local_split(source_text: str, page_texts: Optional[List[str]] = None,
                         generate_answers: bool = False) -> SplitPlan:
    report: dict = {}
    plan = SplitPlan(judge_report=report)
    try:
        if generate_answers:
            report["skipped"] = "generate_answers_on"
            report["trustworthy"] = False
            plan.cover_ok = True
            return plan

        text = _normalize_text(source_text)
        # 卷首答案速查区跳过：若参考答案/【参考详解】等板块标题出现在首个真 1. 锚点之前，
        # 说明前置一小段是答案速查而非题干，切掉它再找锚；找不到 1. 锚点时不跳过，防止整卷被切掉。
        m_head = _ANSWER_ZONE_HEAD_RE.search(text)
        first_one = next((a for a in _find_anchors(text) if a["num"] == 1), None)
        if m_head and first_one is not None and m_head.end() <= first_one["start"]:
            text = text[m_head.end():]
            report["answer_zone_head_skipped"] = m_head.group(0)
        text = _skip_notice_zone(text, report)

        anchors = _find_anchors(text)
        # 前奏/速查噪声清除：真实题号必从 1 起，丢弃首个 num==1 锚点之前的所有锚点
        # （直接干掉「13.充要条件 14.-1」这类卷首答案速查行带来的伪锚）。
        k1 = next((i for i, a in enumerate(anchors) if a["num"] == 1), None)
        if k1 is not None and k1 > 0:
            report["dropped_leading_anchors"] = [a["no"] for a in anchors[:k1]]
            anchors = anchors[k1:]
        report["anchor_count"] = len(anchors)
        if not anchors:
            report["trustworthy"] = False
            return plan

        report["trustworthy"] = False
        if not (MIN_TOTAL <= len(anchors) <= MAX_TOTAL):
            report["total_out_of_range"] = True
            return plan

        reasons: List[str] = []
        shared_hits: List[dict] = []

        # 分节标题及其说明不属于上一题：从上一题尾部切走，单独作为本节前言。
        headers = [m.start() for m in _SECTION_HEADER_RE.finditer(text)]
        for a in anchors:
            cut = [h for h in headers if a["body_start"] < h < a["end"]]
            if cut:
                a["end"] = cut[0]
        sections = []
        for h in headers:
            first_after = next((a["start"] for a in anchors if a["start"] > h), len(text))
            sections.append({"start": h, "preamble": text[h:first_after]})

        lead = text[:anchors[0]["start"]]
        if "$" in lead or "\\begin{" in lead or _image_refs(lead) or _SHARED_CONTEXT_RE.search(lead):
            reasons.append("leading_context")
            report["leading_context"] = True
        for sec in sections:
            if sec["start"] >= anchors[0]["start"] and (
                "$" in sec["preamble"] or _image_refs(sec["preamble"])
                or _SHARED_CONTEXT_RE.search(sec["preamble"])
            ):
                shared_hits.append({"where": "section_preamble", "text": sec["preamble"].strip()[:80]})

        for idx, a in enumerate(anchors):
            seg = _strip_hash_prefix(text[a["body_start"]:a["end"]])
            m = _ANSWER_HEAD_RE.search(seg)
            if m:
                content_raw = seg[:m.start()].rstrip()
                answer_raw = seg[m.start():].strip()
            else:
                content_raw = seg.rstrip()
                answer_raw = ""
            section = next((s for s in reversed(sections) if s["start"] < a["start"]), None)
            block = Block(
                block_id=f"q{a['no']}_{idx}",
                paper_question_no=a["no"],
                start=a["start"], body_start=a["body_start"], end=a["end"],
                content=content_raw, answer_markdown=answer_raw,
                referenced_images=_image_refs(text[a["body_start"]:a["end"]]),
                local_type=_infer_question_type_local(content_raw),
                section_kind=_section_kind(section["preamble"]) if section else "",
            )
            block.risk_flags = _block_gate_flags(block, text)
            block.forced_type = _resolve_choice_type(block) or ""
            if block.forced_type:
                block.local_type = block.forced_type

            raw_seg = text[a["body_start"]:a["end"]]
            if _SHARED_CONTEXT_RE.search(raw_seg):
                block.risk_flags.append("G9_共享条件")
                shared_hits.append({"where": "block", "question_no": a["no"]})
            for ref in _CROSS_REF_RE.finditer(content_raw):
                if ref.group(1) is None or int(ref.group(1)) != a["num"]:
                    block.risk_flags.append("G9_跨题引用")
                    shared_hits.append({"where": "cross_reference", "question_no": a["no"],
                                        "text": ref.group(0)})
                    break
            next_num = anchors[idx + 1]["num"] if idx + 1 < len(anchors) else None
            if next_num != a["num"] + 1 and _inline_next_number(raw_seg, a["num"]):
                block.risk_flags.append("G3_疑似并题")
                report.setdefault("merged_suspected_at", []).append(a["no"])
            plan.all_blocks.append(block)

        if shared_hits:
            reasons.append("shared_context")
            report["shared_context"] = shared_hits
        if report.get("merged_suspected_at"):
            reasons.append("merged_question_suspected")

        multi_blocks = [b.paper_question_no for b in plan.all_blocks if b.forced_type == "multi_choice"]
        if multi_blocks:
            report["multi_choice_blocks"] = multi_blocks

        for i, sec in enumerate(sections):
            m_count = _DECLARED_COUNT_RE.search(sec["preamble"])
            if not m_count:
                continue
            declared = _parse_count(m_count.group(1))
            stop = sections[i + 1]["start"] if i + 1 < len(sections) else len(text) + 1
            actual = sum(1 for b in plan.all_blocks if sec["start"] < b.start < stop)
            if declared and declared != actual:
                reasons.append("section_count_mismatch")
                report.setdefault("section_count_mismatch", []).append(
                    {"declared": declared, "actual": actual})

        monotonic_ok = True
        prev_start = None
        prev_num = None
        groups: List[List[int]] = [[]]
        for blk in plan.all_blocks:
            num = int(blk.paper_question_no)
            between = text[prev_start:blk.start] if prev_start is not None else ""
            if prev_num is not None and num <= prev_num:
                if _SECTION_HEADER_RE.search(between):
                    groups.append([])
                else:
                    monotonic_ok = False
                    report["monotonic_break_at"] = blk.paper_question_no
            groups[-1].append(num)
            prev_num = num
            prev_start = blk.start
        report["monotonic_ok"] = monotonic_ok

        cover_ok = True
        for g in groups:
            if not g:
                continue
            # 每组题号必须从 1 连续编号：首题号被漏识别时整题会混进卷首前言里被丢弃。
            if min(g) != 1 or len(set(g)) != (max(g) - min(g) + 1):
                cover_ok = False
                report["coverage_gap_group"] = [min(g), max(g)]
                break
        plan.cover_ok = bool(monotonic_ok and cover_ok)

        clean = [b for b in plan.all_blocks if not b.risk_flags]
        suspicious = [b for b in plan.all_blocks if b.risk_flags]
        plan.clean_blocks = clean
        plan.suspicious_blocks = suspicious

        total = len(plan.all_blocks)
        preserved_ratio = (len(clean) / total) if total else 0.0
        suspicious_ratio = (len(suspicious) / total) if total else 0.0
        report["local_blocks"] = len(clean)
        report["suspicious_blocks"] = len(suspicious)
        report["preserved_ratio"] = round(preserved_ratio, 3)
        report["suspicious_ratio"] = round(suspicious_ratio, 3)
        report["gate_hits"] = {}
        for b in plan.all_blocks:
            for f in b.risk_flags:
                report["gate_hits"][f] = report["gate_hits"].get(f, 0) + 1

        report["untrusted_reasons"] = sorted(set(reasons))
        trustworthy = bool(
            monotonic_ok
            and plan.cover_ok
            and not reasons
            and preserved_ratio >= MIN_PRESERVED_RATIO
            and suspicious_ratio <= MAX_SUSPICIOUS_RATIO
            and len(suspicious) <= MAX_SUSPICIOUS_ABS
        )
        report["trustworthy"] = trustworthy
        plan.trustworthy = trustworthy
        return plan
    except Exception as exc:
        report["exception"] = repr(exc)
        plan.cover_ok = False
        plan.trustworthy = False
        return plan
