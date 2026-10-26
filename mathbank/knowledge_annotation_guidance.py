"""Pure annotation guidance: task targets, solution evidence and narrow boundaries.

This module never reads a catalogue or changes an identity. Request builders
supply the complete frozen catalogue; optional notes appear only when both a
known stable code and a verified name are present in that same snapshot.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping


KNOWLEDGE_ANNOTATION_GUIDANCE_VERSION = "knowledge-target-evidence.v4"

_TARGET_FIRST_RULE = (
    "\n【先覆盖目标，再选辅助，最后定位教材】只做分类，不输出新解答。\n"
    "1. 通读全部条件、小问与选项。把每个待求/待证/待判断产物分别列在内部清单中，"
    "按“数学对象+要完成的动作+条件范围”在全目录比较候选，而非只匹配主题词。"
    "annotation_targets的goal_quotes是本地从题干提取的原文目标，可直接复制；"
    "不将已给公式、背景名词或设问前置条件当成目标。\n"
    "2. 先为每个能匹配的目标选择最贴切的main，再考虑辅助。"
    "main是直接任务或该任务对应的专门应用能力；求导数或导数值时，导数运算本身是main，"
    "不能因为它先于求和步骤就降为auxiliary。求参数范围、求某事件概率、列完整分布列、"
    "求角大小与证明性质是不同任务。优先检查含参、由性质求参、基本量计算或综合应用等"
    "准确条目；只有名字更长或出现字母参数并不足以选它。"
    "同一目标已选准确具体点时，省去重复的背景定义/泛公式点。\n"
    "3. 逐小问复核main覆盖：不能让某小问只有auxiliary而没有其直接目标的main。"
    "目录确无贴切条目时允许该目标不标，不造码或错套；其他小问仍正常标注。"
    "同一编码服务多个目标时合并target_evidence数组；一个点只要是某问的直接目标，"
    "就按main处理，不因它同时帮助另一问而整体降级。教材先后规则只决定教材位置。\n"
    "4. main覆盖完成后，通常只保留0至2个重要auxiliary，不凑数量。"
    "辅助点须有实际提供的解析/评析步骤和相符的完整点名，不能用“体现了/涉及”来泛化。"
    "常规代入、四则运算、移项、计算普通中间量默认省略；专门方法若本身正是待求任务，"
    "可为main，不能仅凭它是原解核心步骤就升级。"
    "已有导数应用主点时，一次简单求导是先备计算，不再附加基本导数公式/加减运算辅助点；"
    "若题目直接求导数或导数值，导数运算仍是main。"
    "对“利用某性质求值/解题”的条目，须确认该性质已知或已证并实际被用作前提，"
    "不能将正在证明的性质反当成工具。"
    "只有最终答案、选项字母、孤立结论、图片或假设改题的另解说明不算可核对解析，"
    "不得据此猜auxiliary，也不现场补一种解法。\n"
)

_TARGET_EVIDENCE_RULE = (
    "【证据】每项含target_evidence：本题content中含待求/待证对象的逐字连续短句，"
    "一个非空字符串或1至12条不重复原句的数组，不拼接改写。"
    "main的evidence_source为content；auxiliary只能为answer_markdown或review_markdown，"
    "且evidence必须是该字段的真实关键步骤，target_evidence另锚定所服务的题干目标。"
    "选项可作判断目标，不能当解答步骤。优先复制短原句，数学符号、空白与反斜杠保持原文，"
    "JSON转义后仍须逐字相同。不引用目录、题头、其他题或旧标签。"
    "reason用一句短话（通常不超过60字）说明对象、任务及主辅，scope可记小问；"
    "输出前逐项检查全部小问、具体粒度与两类引文。\n"
)

_CONTRAST_EXAMPLES = (
    "【判定边界示例，仅说明规则】\n"
    "- 集合由含参方程定义，需由交集/并集条件反推参数才能确定集合时，"
    "优先比较“含参数的相应集合运算”条目，不仅贴泛交集/并集；"
    "若只需直接列举共同元素，则不凭任意字母硬选含参条目。\n"
    "- 已知函数在某区间单调，要求参数范围：比较目录中的“由单调性求参数”类候选；"
    "要求单调区间才对应求区间的任务。出现单调性并不使两者等同。\n"
    "- 问“某性质成立的充要条件”且选项是参数取值条件时，待求产物仍是参数条件，"
    "应覆盖相应的性质求参能力；不能只选“充要条件”而漏掉数学目标。"
    "只有专门判断两个命题的必要/充分关系时，逻辑关系本身才是独立主目标。\n"
    "- 证明函数奇偶性时，证明中的赋值或比较函数值，不等于利用已经成立的奇偶性求值；"
    "证明完成后另一问再使用该性质，才可能是实际辅助。\n"
    "- 数列题利用前n项和关系反求首项、公差等基本量：比较基本量计算与公式条目，"
    "不能仅因解中写出求和公式就停在公式标签；若直接考查求和公式本身则另论。\n"
    "- 已给椭圆方程而要求判断与向量有关的几何关系：已给方程不等于要求求椭圆标准方程，"
    "应比较实际几何目标与目录中的综合条目；只出现向量符号也不自动构成综合考查。\n"
)

# Each note is a prompt distinction, never a keyword-based acceptance/rejection
# guard. Names are tied to shipped, verified stable identities rather than a
# particular question, old label, model response or expected answer.
_SEMANTIC_BOUNDARIES = (
    (
        "1932",
        ("利用二项分布求分布列",),
        "本点的任务是利用二项分布列出随机变量全部可能取值及相应概率。"
        "只求一个事件、一个取值或某个范围的概率，不等于求分布列；"
        "不能仅因用到二项概率公式或别名“二项分布”就把本点当作通用概率标签。"
        "仍需与完整目录中更贴合所求事件的条目比较。",
    ),
    (
        "1097",
        ("二次函数零点与一元二次方程解的关系",
         "二次函数零点与一元二次方程解的关系【人教B、苏教、湘教】"),
        "本点针对二次函数零点与对应一元二次方程解之间的联系。"
        "仅把给定数值代入方程验证或求解方程，不等于考查函数零点与方程解的对应关系。"
        "求二次不等式解集时仅把方程根作为中间计算结果，"
        "不足以证明题目直接考查这种对应关系；"
        "应区分求方程根、判断函数零点以及解不等式的实际目标。",
    ),
    (
        "5030",
        ("分层随机抽样的样本均值",),
        "本点针对由各层数据或各层均值及权重确定、估计样本整体均值。"
        "求每层抽取人数、样本量或抽样比属于抽样方案和数量关系，"
        "不等于求样本均值；不能把分层抽样背景本身当作均值考查证据。",
    ),
)


def _known_names(point: Mapping) -> set[str]:
    names = [point.get("name"), point.get("original_name")]
    aliases = point.get("aliases", ())
    if isinstance(aliases, (list, tuple)):
        names.extend(aliases)
    return {value.strip() for value in names if isinstance(value, str) and value.strip()}


def build_verified_boundary_notes(catalog: Iterable[Mapping] | None = None) -> str:
    """Return only scope notes whose identities exist in this snapshot."""
    installed = {
        point["code"]: point for point in (() if catalog is None else catalog)
        if isinstance(point, Mapping) and isinstance(point.get("code"), str)
    }
    notes = []
    for code, expected_names, guidance in _SEMANTIC_BOUNDARIES:
        point = installed.get(code)
        if point is None or not _known_names(point).intersection(expected_names):
            continue
        notes.append([code, guidance])
    if notes:
        return (
            "【当前目录中少数易混条目的语义边界】以下说明不新增点码、不改变名称，"
            "也不预先指定本题应选的条目；仍按原文目标判断：\n"
            + json.dumps(notes, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    return ""


def build_knowledge_annotation_guidance(catalog: Iterable[Mapping] | None = None) -> str:
    """Return target-first instructions, plus notes verified in this snapshot."""
    return (_TARGET_FIRST_RULE + _TARGET_EVIDENCE_RULE + _CONTRAST_EXAMPLES
            + build_verified_boundary_notes(catalog))
