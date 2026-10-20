"""Only an exact known formula-lock input may migrate without rereading PDF."""
from copy import deepcopy
import hashlib

import pytest

from mathbank.content_locks import lock_visible_math
from mathbank.pdf_task_resume import is_legacy_pdf_split_input, is_compatible_pdf_split_input


def legacy(source="1. 求 $x+1$。", namespace="savedsource123456"):
    locked, _ = lock_visible_math(source, namespace)
    return {"source_markdown":source, "model_source":locked,
            "model_source_sha256":hashlib.sha256(locked.encode()).hexdigest()}


def test_identical_transcript_known_formula_protocol_can_upgrade():
    old = legacy()
    before = deepcopy(old)
    assert is_legacy_pdf_split_input(old,old["source_markdown"],old["source_markdown"],"savedsource123456")
    assert old == before


@pytest.mark.parametrize("change", ["source", "hash", "namespace", "body", "new_locked", "missing"])
def test_changed_or_unrecognized_input_cannot_upgrade(change):
    old=legacy();source=old["source_markdown"];model=source;namespace="savedsource123456"
    if change=="source":source+="改题";model=source
    elif change=="hash":old["model_source_sha256"]="0"*64
    elif change=="namespace":namespace="another-source"
    elif change=="body":
        old["model_source"]+="追加正文"
        old["model_source_sha256"]=hashlib.sha256(old["model_source"].encode()).hexdigest()
    elif change=="new_locked":model=old["model_source"]
    else:old.pop("model_source")
    assert not is_legacy_pdf_split_input(old,source,model,namespace)


@pytest.mark.parametrize('old_kind',['plain','task_lock','same_project_lock'])
def test_exact_old_plain_or_recognized_namespace_can_move_to_project_protocol(old_kind):
    source = '1. 求 $x+1$。\n原代码`mathbank-math`也必须保留。'
    old_namespace = 'old-task-123'
    target_namespace = 'project-456'
    old_model = source if old_kind=='plain' else lock_visible_math(source,
        target_namespace if old_kind=='same_project_lock' else old_namespace)[0]
    previous = {'source_markdown':source,'model_source':old_model,
        'model_source_sha256':hashlib.sha256(old_model.encode()).hexdigest()}
    before = deepcopy(previous)
    assert is_compatible_pdf_split_input(previous,source,lock_visible_math(source,target_namespace)[0],
        previous_namespace=target_namespace if old_kind=='same_project_lock' else old_namespace,
        target_namespace=target_namespace)
    assert previous==before


@pytest.mark.parametrize('change',['source','hash','unknown_old_wrapper','changed_old_math','wrong_old_namespace',
    'unknown_target_wrapper','wrong_target_namespace','malformed_namespace','missing_model'])
def test_protocol_migration_never_accepts_changed_source_or_unrecognized_model_input(change):
    source = '1. 求 $x+1$。'
    old_namespace,target_namespace='old-task-123','project-456'
    previous = legacy(source,old_namespace)
    target = lock_visible_math(source,target_namespace)[0]
    if change=='source':source+=' 新条件'
    elif change=='hash':previous['model_source_sha256']='0'*64
    elif change in {'unknown_old_wrapper','changed_old_math'}:
        previous['model_source'] = ('UNRECOGNIZED '+source if change=='unknown_old_wrapper'
                                    else previous['model_source'].replace('$x+1$','$x+2$'))
        previous['model_source_sha256']=hashlib.sha256(previous['model_source'].encode()).hexdigest()
    elif change=='wrong_old_namespace':old_namespace='different-task'
    elif change=='unknown_target_wrapper':target='UNKNOWN '+source
    elif change=='wrong_target_namespace':target_namespace='different-project'
    elif change=='malformed_namespace':target_namespace='../../unknown'
    else:previous.pop('model_source')
    assert not is_compatible_pdf_split_input(previous,source,target,
        previous_namespace=old_namespace,target_namespace=target_namespace)
