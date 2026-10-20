"""Missing identity is resolved by source content, never by output position."""
from copy import deepcopy
import json

import pytest

from mathbank.content_locks import lock_visible_math
from mathbank.pdf_source_numbers import resolve_pdf_scope_source_numbers


def fixture():
    bodies = [f"第{number}题独有条件是$x_{{{number}}}={number}$，求该数加一的结果。" + "原题条件必须保留。"*35
              for number in range(1,20)]
    source = "一、解答题\n" + "\n\n".join(f"{i}. {body}" for i, body in enumerate(bodies,1))
    source += "\n# 参考答案与解析\n" + "\n".join(f"{i}. 原版答案是${i+1}$。" for i in range(1,20))
    # Neutral title padding is outside question identity and has no numbers.
    source = "原卷说明。"*600 + "\n" + source
    rows = [{"content":body,"answer_markdown":"[EXTRACTED_ORIGINAL]原答",
             "source_review":{"required":True,"reasons":["保留原提示"]}, "unknown_audit":{"kept":i}}
            for i,body in enumerate(bodies,1)]
    return source,rows


def resolve(rows,source,expected,**kwargs):
    return resolve_pdf_scope_source_numbers(rows,source,"project_namespace",expected,**kwargs)


def test_real_original_matcher_proves_all_nineteen_without_array_order():
    source,rows=fixture();rows=rows[10:]+rows[:10];before=deepcopy(rows);diagnostics={}
    result=resolve(rows,source,list(range(1,20)),diagnostics=diagnostics)
    assert [row["source_number"] for row in result]==list(range(11,20))+list(range(1,11))
    assert rows==before
    assert [{k:v for k,v in row.items() if k!="source_number"} for row in result]==before
    report=diagnostics["pdf_source_number_resolution"]
    assert report["inferred_count"]==19 and report["source_verified"] is False
    assert len({(m["source_start"],m["source_end"]) for m in report["proven_matches"]})==19


def test_each_cached_scope_can_be_resolved_independently():
    source,rows=fixture()
    first=resolve(list(reversed(rows[:11])),source,list(range(1,12)))
    second=resolve(list(reversed(rows[11:])),source,list(range(12,20)))
    assert [q["source_number"] for q in first]==list(range(11,0,-1))
    assert [q["source_number"] for q in second]==list(range(19,11,-1))


def test_null_optional_numbers_can_be_inferred_without_altering_any_other_value():
    source,rows=fixture();rows[0]["source_number"]=None
    before=deepcopy(rows)
    result=resolve(rows,source,list(range(1,20)))
    assert result[0]["source_number"]==1 and before[0]["source_number"] is None
    assert result[0]["source_review"]==before[0]["source_review"]


@pytest.mark.parametrize("value",["1",1.0,True,99])
def test_existing_invalid_value_is_not_coerced_or_overwritten(value):
    source,rows=fixture();rows[0]["source_number"]=value
    with pytest.raises(ValueError,match="有效整数"):
        resolve(rows,source,list(range(1,20)))
    assert rows[0]["source_number"]==value


def test_existing_integer_conflicting_with_unique_source_is_rejected():
    source,rows=fixture()
    for index,row in enumerate(rows):row["source_number"]=(index+1)%19+1
    with pytest.raises(ValueError,match="冲突"):
        resolve(rows,source,list(range(1,20)))


def test_existing_valid_numbers_with_uncertain_source_keep_original_pipeline_semantics():
    source,rows=fixture()
    for index,row in enumerate(rows):
        row["source_number"]=index+1
        row["content"]=f"未知输出正文{index}，保留原流程的来源疑点。"
    diagnostics={};result=resolve(rows,source,list(range(1,20)),diagnostics=diagnostics)
    assert result==rows and diagnostics["pdf_source_number_resolution"]["inferred_count"]==0
    assert diagnostics["pdf_source_number_resolution"]["retained_unproved_model_numbers"]==list(range(1,20))


def test_missing_number_without_unique_source_is_not_filled_by_position():
    source,rows=fixture();rows[0]["content"]="全新正文无法识别来源。"
    with pytest.raises(ValueError,match="数组位置"):
        resolve(rows,source,list(range(1,20)))


def test_two_candidate_copies_cannot_share_one_source_range():
    source,rows=fixture();rows[1]=deepcopy(rows[0])
    with pytest.raises(ValueError):resolve(rows,source,list(range(1,20)))


def test_source_match_to_other_scope_cannot_fill_missing_identity():
    source,rows=fixture()
    with pytest.raises(ValueError,match="超出"):
        resolve(rows[11:],source,list(range(1,9)))


def test_scope_must_have_complete_unique_integer_coverage():
    source,rows=fixture()
    for bad in ([1,1], [True], ["1"], [1,99], []):
        with pytest.raises(ValueError):resolve(rows[:len(bad)],source,bad)


def test_original_matcher_formula_or_review_changes_never_leak_to_returned_text():
    source,rows=fixture();locked,locks=lock_visible_math(source,"project_namespace")
    # A real original formula ID is resolved only on the disposable matcher copy.
    rows[0]["content"]=rows[0]["content"].replace("$x_{1}=1$",f"[[{locks[0].lock_id}]]")
    before=deepcopy(rows)
    result=resolve(rows,source,list(range(1,20)))
    assert result[0]["content"]==before[0]["content"] and "[[MBM_" in result[0]["content"]
    assert result[0]["answer_markdown"]==before[0]["answer_markdown"]
    assert result[0]["source_review"]==before[0]["source_review"]


def test_an_answer_outside_the_stem_cannot_prove_a_missing_question_number(monkeypatch):
    import mathbank.pdf_source_numbers as module
    source,rows=fixture()
    answer=source.index("# 参考答案与解析")
    monkeypatch.setattr(module,"reconcile_visible_math",lambda *a,**kw:{"source_matches":[
        {"field":"content","question_index":i,"source_number":i+1,"source_start":answer+1,"source_end":len(source)}
        for i in range(19)]})
    with pytest.raises(ValueError,match="数组位置"):
        resolve(rows,source,list(range(1,20)))
