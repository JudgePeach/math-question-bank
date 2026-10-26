"""Read-only inputs and strict response envelopes for single-question tagging."""
from __future__ import annotations

import base64
import json

from .ai_providers import supports_classification_images
from .ai_timeouts import AI_REQUEST_TIMEOUT_SECONDS
from .asset_security import (
    AssetSecurityError, InvalidImageError, normalize_raster_image,
    read_stream_limited, resolve_upload_asset, UploadTooLargeError, _ImageOutputTooLargeError,
)
from .processing_limits import CLASSIFY_IMAGES, CLASSIFY_IMAGE_BYTES
from .knowledge_image_evidence import ImageEvidenceManifest, owned_image_references
from .paths import UPLOADS_DIR
from .pdf_vision_request import completion_content
from .source_review_results import parse_review_response
from .knowledge_solution_context import solution_context
from .knowledge_annotation_targets import (
    extract_knowledge_annotation_targets, knowledge_annotation_targets_for_prompt,
)


MAX_CLASSIFY_IMAGES = CLASSIFY_IMAGES
MAX_CLASSIFY_IMAGE_BYTES = CLASSIFY_IMAGE_BYTES
CLASSIFICATION_TIMEOUT_SECONDS = AI_REQUEST_TIMEOUT_SECONDS


def classification_supports_images(provider):
    """Reuse central capabilities only when they describe the same identity."""
    return supports_classification_images(provider)


def build_classification_user_message(question, provider, *, uploads_dir=UPLOADS_DIR,
                                      url_prefix="static/uploads"):
    """Attach bounded referenced local rasters; never fetch URLs or restore files."""
    references = owned_image_references(question)
    images, visible = [], []
    manifest = ImageEvidenceManifest(question)
    image_inputs = []
    reason_messages = {"model_no_images": "当前分类模型尚未确认支持图像输入",
        "image_limit": "已达到本次分类附图数量上限", "missing_file": "本地图片文件不存在",
        "input_too_large": "原图片超过本次分类单图大小上限", "output_too_large": "规范化图片超过本次分类单图大小上限",
        "invalid_path": "图片引用不属于可安全读取的本地上传路径", "invalid_image": "图片格式无效或无法安全解码",
        "unreadable_file": "本地图片暂不可读取"}
    for ordinal, (reference, owners) in enumerate(references.items(), 1):
        failure = None
        if not classification_supports_images(provider):
            failure = "model_no_images"
        elif len(visible) >= MAX_CLASSIFY_IMAGES:
            failure = "image_limit"
        else:
            try:
                path = resolve_upload_asset(reference, uploads_dir=uploads_dir,
                                            url_prefix=url_prefix, require_file=False)
                if not path.is_file():
                    failure = "missing_file"
                else:
                    with path.open("rb") as stream:
                        raw = read_stream_limited(stream, MAX_CLASSIFY_IMAGE_BYTES)
                    normalized = normalize_raster_image(raw, max_output_bytes=MAX_CLASSIFY_IMAGE_BYTES)
            except AssetSecurityError:
                failure = "invalid_path"
            except UploadTooLargeError:
                failure = "input_too_large"
            except _ImageOutputTooLargeError:
                failure = "output_too_large"
            except (InvalidImageError, ValueError):
                failure = "invalid_image"
            except OSError:
                failure = "unreadable_file"
        detail = {"index": ordinal, "fields": list(dict.fromkeys(owner["field"] for owner in owners)),
                  "sent": failure is None, "reason": failure, "message": reason_messages.get(failure, "已提供给分类模型")}
        image_inputs.append(detail)
        if failure is None:
            visible.append(reference)
            safe_path = url_prefix.strip("/") + "/" + path.relative_to(uploads_dir).as_posix()
            image_id = manifest.add_sent_image(path=safe_path, file_path=path, raw=raw, sent=normalized.data,
                                               references=owners, max_bytes=MAX_CLASSIFY_IMAGE_BYTES)
            detail.update(image_id=image_id, path=safe_path)
            images.extend([{"type": "text", "text": "以下图像对应题文引用：" + reference},
                           {"type": "image_url", "image_url": {
                               "url": "data:" + normalized.media_type + ";base64,"
                                      + base64.b64encode(normalized.data).decode("ascii")}}])
    reasoning = solution_context(question)
    target_plan = extract_knowledge_annotation_targets(question.get("content", ""))
    context = {"answer_available": bool(question.get("answer_markdown", "").strip()),
               "review_available": bool(question.get("review_markdown", "").strip()),
               "solution_context": reasoning,
               "reasoning_available": reasoning["has_reasoning"],
               "solution_basis": "provided_text_not_independently_verified",
               "referenced_image_count": len(references), "visible_image_count": len(visible),
               "visible_images": visible,
               "image_inputs": image_inputs, "image_manifest": manifest,
               "image_state": ("none" if not references else "available" if len(visible) == len(references)
                               else "partial" if visible else "unavailable")}
    envelope = {**question, "classification_context": context,
                "annotation_targets": knowledge_annotation_targets_for_prompt(target_plan)}
    text = json.dumps(envelope, ensure_ascii=False)
    warnings = []
    if len(visible) != len(references):
        warnings.append("部分题图未提供给分类模型，本次仅按可见文字和已提供图像标注，请核对依赖图形的考点。")
        warnings.extend(f"第{item['index']}幅图像未提供：{item['message']}。" for item in image_inputs if not item["sent"])
    return ({"role": "user", "content": [{"type": "text", "text": text}, *images] if images else text},
            context, warnings)


def parse_classification_response(body):
    """Reject truncated, duplicate-key and non-object output without text repair."""
    raw = completion_content(body, "AI分类", max_chars=50000, allow_missing_finish=True)
    try:
        result = parse_review_response(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("AI分类返回的JSON无效，请保留当前编辑后重试。") from exc
    if not isinstance(result, dict):
        raise ValueError("AI分类返回的结果不是对象，请保留当前编辑后重试。")
    usage = body.get("usage")
    counts = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
              if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0}
    return result, counts or None
