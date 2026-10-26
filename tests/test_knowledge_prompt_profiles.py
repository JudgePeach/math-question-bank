"""Unknown models keep the frozen safe fallback; opt-in profiles stay pure."""
from copy import deepcopy
import hashlib
import json

import pytest

from mathbank.ai_knowledge_points import build_ai_knowledge_prompt
from mathbank.prompts import build_classification_system_prompt


CATALOG = [{"code": "1932", "name": "利用二项分布求分布列", "original_name": "二项分布", "aliases": ["二项分布"]},
           {"code": "local:teacher", "name": "老师函数目标", "original_name": "老师来源旧名", "aliases": ["函数"]}]
TREE = {"必修一": {"函数": ["概念"]}}


def directory(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("\n[") + 1:])[0]


def test_conservative_is_the_compatible_default_and_target_profile_is_explicit():
    original = deepcopy(CATALOG)
    assert directory(build_ai_knowledge_prompt(CATALOG)) == [
        ["1932", "利用二项分布求分布列", "二项分布"],
        ["local:teacher", "老师函数目标", "老师来源旧名", "函数"]]
    assert directory(build_ai_knowledge_prompt(CATALOG, profile="target_first")) == [
        ["1932", "利用二项分布求分布列"], ["local:teacher", "老师函数目标"]]
    assert CATALOG == original


def test_both_profiles_keep_the_same_five_field_response_contract():
    for profile in ("conservative", "target_first"):
        prompt = build_classification_system_prompt(TREE, CATALOG, knowledge_profile=profile)
        schema = json.loads(prompt[prompt.rindex("{\n"):].split("\n}", 1)[0] + "\n}")
        assert set(schema) == {"compulsory", "chapter", "knowledge", "question_form", "knowledge_points"}
        assert len(directory(prompt)) == len(CATALOG)


def test_template_markers_in_teacher_data_are_never_recursively_expanded():
    catalog = [{"code": "local:literal", "name": "<MB_CURRICULUM_TEXT>",
                "original_name": "<MB_KNOWLEDGE_PROMPT>", "aliases": ["<MB_KNOWLEDGE_CATALOG_JSON>"]}]
    tree = {"<MB_KNOWLEDGE_PROMPT>": {"chapter": ["section"]}}
    prompt = build_classification_system_prompt(tree, catalog)
    assert directory(prompt) == [["local:literal", "<MB_CURRICULUM_TEXT>", "<MB_KNOWLEDGE_PROMPT>", "<MB_KNOWLEDGE_CATALOG_JSON>"]]
    assert "- <MB_KNOWLEDGE_PROMPT>:" in prompt


@pytest.mark.parametrize("profile", ["", "unknown", None, "TARGET_FIRST"])
def test_unknown_profile_is_explicitly_rejected(profile):
    with pytest.raises(ValueError):build_ai_knowledge_prompt(CATALOG, profile=profile)
    with pytest.raises(ValueError):build_classification_system_prompt(TREE, CATALOG, knowledge_profile=profile)
