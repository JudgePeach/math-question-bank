"""JSON checkpoints for resuming one server-owned PDF import task.

No checkpoint certifies OCR correctness or a private native-source proof. It
only preserves already accepted stage output and binds its original assets.
"""
from copy import deepcopy
import hashlib
import json
import re


SCHEMA = "mathbank.pdf-task-resume.v1"


def is_compatible_pdf_split_input(previous, source_markdown, model_source, *,
                                  previous_namespace, target_namespace):
    """Recognize unchanged plain/task-lock input -> exact project-lock input.

    This is only a protocol recognizer after server-owned checkpoint validation.
    The caller must archive the previous input and accepted result before using
    it. It neither rewrites the transcript nor certifies any question output.
    """
    if (not isinstance(previous, dict) or not isinstance(source_markdown, str)
            or previous.get("source_markdown") != source_markdown
            or not isinstance(model_source, str)
            or not isinstance(target_namespace, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", target_namespace)):
        return False
    original_model = previous.get("model_source")
    if (not isinstance(original_model, str)
            or previous.get("model_source_sha256") != hashlib.sha256(original_model.encode("utf-8")).hexdigest()):
        return False
    from mathbank.content_locks import lock_visible_math
    target = lock_visible_math(source_markdown,target_namespace)[0]
    if model_source != target:
        return False
    if original_model == source_markdown:
        return True
    if (not isinstance(previous_namespace,str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",previous_namespace)):
        return False
    return original_model == lock_visible_math(source_markdown,previous_namespace)[0]


def is_legacy_pdf_split_input(previous, source_markdown, model_source, source_namespace):
    """Recognize only an exact, source-preserving formula-lock transition.

    The original checkpoint must already have passed prepare_pdf_checkpoint.
    A different document, transcript, unknown wrapper or broken hash is never
    a protocol upgrade. The caller archives the old packet before replacing it.
    """
    if (not isinstance(previous, dict) or not isinstance(source_markdown, str)
            or model_source != source_markdown
            or previous.get("source_markdown") != source_markdown
            or not isinstance(source_namespace, str)):
        return False
    old_model_source = previous.get("model_source")
    if not isinstance(old_model_source, str) or old_model_source == source_markdown:
        return False
    if previous.get("model_source_sha256") != hashlib.sha256(old_model_source.encode("utf-8")).hexdigest():
        return False
    from mathbank.content_locks import lock_visible_math
    expected, _ = lock_visible_math(source_markdown, source_namespace)
    return expected == old_model_source


def validate_pdf_transcript_checkpoint(state, document_sha256):
    """Validate a sealed OCR source; selected image bytes are checked by caller.

    Unlike task resume this does not require unrelated old page/detail caches.
    A page can use an identical current PNG as its pixel witness after those
    obsolete files were deleted; referenced figures still need their own bytes.
    """
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or state.get("document_sha256") != document_sha256
            or state.get("state_sha256") != _digest(state)
            or not isinstance(state.get("task_id"), str)
            or not isinstance(state.get("pages"), dict)
            or not isinstance(state.get("assets"), dict)):
        raise ValueError("历史识图检查点的原文件绑定无效。")
    indices = state.get("page_indices")
    if (not isinstance(indices, list) or not indices
            or any(type(index) is not int or index < 0 for index in indices)
            or indices != sorted(set(indices))):
        raise ValueError("历史识图检查点的原页号无效。")
    return deepcopy(state)


def _digest(state):
    value = {key: item for key, item in state.items() if key != "state_sha256"}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def prepare_pdf_checkpoint(state, *, task_id, document_sha256, page_indices, strategy,
                           verify_suspicions, resolve_asset):
    identity = {"schema": SCHEMA, "task_id": task_id, "document_sha256": document_sha256,
                "page_indices": list(page_indices), "strategy": strategy,
                "verify_suspicions": bool(verify_suspicions)}
    if state is None:
        return {**identity, "pages": {}, "assets": {}, "run": 0}
    if (not isinstance(state, dict) or any(state.get(key) != value for key, value in identity.items())
            or state.get("state_sha256") != _digest(state)
            or not isinstance(state.get("pages"), dict) or not isinstance(state.get("assets"), dict)):
        raise ValueError("PDF 恢复缓存与原文件、页码或解析设置不一致，请重新上传。")
    result = deepcopy(state)
    for url, receipt in result["assets"].items():
        if not isinstance(url, str) or not isinstance(receipt, dict):
            raise ValueError("PDF 恢复图片记录无效，请重新上传。")
        try:
            path = resolve_asset(url)
            if path.is_symlink() or not path.is_file():
                raise ValueError("missing asset")
            data = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError("PDF 恢复所需原页或配图已丢失，请重新上传。") from exc
        if receipt != {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}:
            raise ValueError("PDF 恢复所需原页或配图已变化，请重新上传。")
    selected = {str(index) for index in page_indices}
    if set(result["pages"]) - selected or type(result.get("run")) is not int:
        raise ValueError("PDF 恢复页面记录无效，请重新上传。")
    for record in result["pages"].values():
        if (not isinstance(record, dict) or not isinstance(record.get("markdown"), str)
                or not record["markdown"].strip()
                or (record.get("joint") is not None and not isinstance(record["joint"], dict))):
            raise ValueError("PDF 恢复页面正文记录无效，请重新上传。")
    result["run"] += 1
    result.pop("state_sha256", None)
    return result


def seal_pdf_checkpoint(state, *, assets, resolve_asset):
    """Hash each newly registered immutable task asset once, then seal JSON."""
    for url in assets:
        if url in state["assets"]:
            continue
        path = resolve_asset(url)
        if path.is_symlink() or not path.is_file():
            raise ValueError("PDF 恢复缓存中的图片不可用。")
        data = path.read_bytes()
        state["assets"][url] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    snapshot = deepcopy(state)
    snapshot["state_sha256"] = _digest(snapshot)
    return snapshot
