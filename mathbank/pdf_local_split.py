# -*- coding: utf-8 -*-
"""PDF 本地切块裁判（纯本地、零网络、零 AI、可离线单测）。

把整卷源文按**行首题号**切成「干净题块」+「可疑题块」，并给出整卷级裁决 trustworthy。
当前仅作为可选诊断接入（MATHBANK_PDF_LOCAL_SPLIT=1），不改变原有 AI 拆题流程。

本模块**顶层绝不 import main / parsing_pipeline**（那会触发 main 抢 runtime 锁，
离线脚本无法运行）。所需的纯原语一律在本文件内镜像实现；确需复用的
`pdf_inspector_helper` 亦不 import main，可安全加载。
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
_QUESTION_NO_RE = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]*[.．、][ \t]*(?![0-9])")
_PAGE_MARKER_RE = re.compile(r"[ \t]*<!--\s*MATHBANK_PDF_PAGE:\s*\d+\s*-->\n?", re.IGNORECASE)
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
_FIRST_ONE_ANCHOR_RE = re.compile(r"(?m)^[ \t]*1[ \t]*[.．、][ \t]*(?![0-9])")
# 选项标号（行首或行内），用于本地兜底判型；要求标号后紧跟内容或行尾/换行。
_OPTION_LABEL_ANY_RE = re.compile(r"(?:^|[\s（(])([A-H])[ \t]*[.．、](?=[ \t]*\S|[ \t]*(?:$|\n))")
_SECTION_HEADER_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:[一二三四五六七八九十]{1,3}\s*[、.．，,]\s*)?"
    r"(?:单项选择|多项选择|选择题|单选题|填空题|解答题|必考题?|选考题?|判断题|卷[ⅠⅡ])"
)
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_INCIMG_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")

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

    def to_parsed_question(self, classification: Optional[dict]) -> dict:
        cls = classification or {}
        q_type = (cls.get("question_type") or "").strip() or self.local_type
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
    matches = list(_QUESTION_NO_RE.finditer(text))
    for i, m in enumerate(matches):
        num = int(m.group(1))
        if not (1 <= num <= 999):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        anchors.append({
            "no": str(num), "num": num,
            "start": m.start(), "body_start": m.end(), "end": end,
        })
    return anchors


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
        # 说明前置一小段是答案速查而非题干，切掉它再找锚；否则保持原样（保守）。
        m_head = _ANSWER_ZONE_HEAD_RE.search(text)
        m_one = _FIRST_ONE_ANCHOR_RE.search(text)
        if m_head and (m_one is None or m_head.end() <= m_one.start()):
            text = text[m_head.end():]
            report["answer_zone_head_skipped"] = m_head.group(0)

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

        for idx, a in enumerate(anchors):
            seg = _strip_hash_prefix(text[a["body_start"]:a["end"]])
            m = _ANSWER_HEAD_RE.search(seg)
            if m:
                content_raw = seg[:m.start()].rstrip()
                answer_raw = seg[m.start():].strip()
            else:
                content_raw = seg.rstrip()
                answer_raw = ""
            block = Block(
                block_id=f"q{a['no']}_{idx}",
                paper_question_no=a["no"],
                start=a["start"], body_start=a["body_start"], end=a["end"],
                content=content_raw, answer_markdown=answer_raw,
                referenced_images=_image_refs(text[a["body_start"]:a["end"]]),
                local_type=_infer_question_type_local(content_raw),
            )
            block.risk_flags = _block_gate_flags(block, text)
            plan.all_blocks.append(block)

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
            if len(set(g)) != (max(g) - min(g) + 1):
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

        trustworthy = bool(
            monotonic_ok
            and plan.cover_ok
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
