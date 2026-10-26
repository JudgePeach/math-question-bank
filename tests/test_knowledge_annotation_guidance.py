"""Prompt semantics are gated by the actual frozen catalogue identities."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from mathbank.knowledge_annotation_guidance import (
    KNOWLEDGE_ANNOTATION_GUIDANCE_VERSION, build_knowledge_annotation_guidance,
)


BOUNDARY_MARKER = "【当前目录中少数易混条目的语义边界】"


def notes(prompt):
    if BOUNDARY_MARKER not in prompt:
        return {}
    return dict(json.loads(prompt.rsplit("\n", 2)[-2]))


def row(code, name, *, original_name="", aliases=None):
    return {"code": code, "name": name, "original_name": original_name,
            "aliases": [] if aliases is None else aliases}


def test_full_shipped_catalogue_exposes_only_verified_narrow_boundaries():
    data = json.loads((Path(__file__).parents[1] / "mathbank/resources/knowledge_points.json").read_text())
    catalog = data["points"]
    frozen = deepcopy(catalog)
    result = notes(build_knowledge_annotation_guidance(catalog))
    assert set(result) == {"1932", "1097", "5030"}
    assert "全部可能取值及相应概率" in result["1932"]
    assert "求二次不等式解集" in result["1097"]
    assert "每层抽取人数" in result["5030"]
    assert catalog == frozen


@pytest.mark.parametrize("catalog", [
    None, [],
    [row("local:teacher-binomial", "利用二项分布求分布列")],
    [row("1932", "老师改成另一种内容")],
    [row("1932", "二项分布", aliases=["二项分布"])],
    [row("5030", "分层随机抽样")],
    [row("1097", "解二次不等式")],
])
def test_absent_or_semantically_changed_identity_never_injects_a_hint(catalog):
    assert notes(build_knowledge_annotation_guidance(catalog)) == {}


def test_user_display_name_can_change_while_verified_original_preserves_identity():
    catalog = [row("1932", "老师的短名称", original_name="利用二项分布求分布列")]
    frozen = deepcopy(catalog)
    assert set(notes(build_knowledge_annotation_guidance(catalog))) == {"1932"}
    assert catalog == frozen


def test_exact_verified_alias_is_supported_without_treating_a_string_as_alias_list():
    valid = row("5030", "老师的简称", aliases=["分层随机抽样的样本均值"])
    invalid = row("5030", "老师的简称", aliases="分层随机抽样的样本均值")
    assert set(notes(build_knowledge_annotation_guidance([valid]))) == {"5030"}
    assert notes(build_knowledge_annotation_guidance([invalid])) == {}


def test_catalogue_order_does_not_change_guidance_and_no_global_catalogue_is_required():
    catalog = [row("5030", "分层随机抽样的样本均值"), row("1932", "利用二项分布求分布列")]
    assert build_knowledge_annotation_guidance(catalog) == build_knowledge_annotation_guidance(reversed(catalog))
    generic = build_knowledge_annotation_guidance()
    assert KNOWLEDGE_ANNOTATION_GUIDANCE_VERSION
    assert "target_evidence" in generic and "1至12条不重复原句" in generic
    assert "main的evidence_source为content" in generic
    assert "auxiliary只能为answer_markdown或review_markdown" in generic
    assert "孤立结论" in generic and "图片或假设改题" in generic
    assert "不引用目录、题头、其他题或旧标签" in generic
    assert "求导数或导数值时，导数运算本身是main" in generic
    assert "不能让某小问只有auxiliary" in generic


def test_ai_directory_keeps_all_identities_without_broad_search_aliases():
    from mathbank.ai_knowledge_points import build_ai_knowledge_prompt
    catalog = [row("1932", "利用二项分布求分布列", original_name="二项分布", aliases=["二项分布"]),
               row("local:custom", "老师定义的函数目标", original_name="来源旧名", aliases=["函数"])]
    frozen = deepcopy(catalog)
    prompt = build_ai_knowledge_prompt(catalog, profile="target_first")
    start = prompt.index("\n[") + 1
    directory, _end = json.JSONDecoder().raw_decode(prompt[start:])
    assert directory == [["1932", "利用二项分布求分布列"], ["local:custom", "老师定义的函数目标"]]
    assert catalog == frozen
