"""Request-local image bindings; model OCR is observable, never independently verified."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .asset_security import read_stream_limited
from .question_assets import _IMAGE, image_reference_spans
from .knowledge_solution_context import evidence_is_reasoning, solution_context, image_reference_is_actual


MAX_IMAGE_TRANSCRIPTION = 8000
MAX_IMAGE_STEP_QUOTE = 600
IMAGE_REASON_NOTICE = "图片解析转录，未经独立核验"
SOURCE_FIELDS = ("content", "answer_markdown", "review_markdown")


def _sha(value):
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _question_digest(question):
    return _sha(json.dumps({field: question.get(field, "") if isinstance(question.get(field, ""), str) else ""
                           for field in SOURCE_FIELDS},
                           ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def owned_image_references(question):
    """Use the normal literal-aware scanner, retaining exact original wrappers."""
    references = {}
    for field in SOURCE_FIELDS:
        source = question.get(field, "")
        if not isinstance(source, str):
            continue
        wrappers = list(_IMAGE.finditer(source))
        for start, end, path in image_reference_spans(source, local_only=False):
            wrapper = next((match for match in wrappers if match.start() <= start < end <= match.end()), None)
            if wrapper is not None:
                reference = {"field": field, "quote": wrapper.group()}
                if reference not in references.setdefault(path, []):
                    references[path].append(reference)
    return references


@dataclass(frozen=True)
class _Binding:
    image_id: str
    sent_sha256: str
    file_path: Path
    raw_sha256: str
    max_bytes: int
    references: tuple[tuple[str, str], ...]


class ImageEvidenceManifest(list):
    """Only the local builder can create bindings; serialized model copies cannot."""
    def __init__(self, question):
        super().__init__()
        self._question_sha256 = _question_digest(question)
        self._bindings = ()

    def add_sent_image(self, *, path, file_path, raw, sent, references, max_bytes):
        digest = hashlib.sha256(sent).hexdigest()
        image_id = f"classimg_{len(self) + 1}_{digest[:16]}"
        binding = _Binding(image_id, digest, Path(file_path), hashlib.sha256(raw).hexdigest(),
                           max_bytes, tuple((ref["field"], ref["quote"]) for ref in references))
        self._bindings = (*self._bindings, binding)
        self.append({"image_id": image_id, "sha256": digest, "path": path,
                     "references": [dict(ref) for ref in references]})
        return image_id


def validate_image_step(question, point, proof, manifest):
    """Validate provenance and inspectable OCR steps, not pixel/text equivalence."""
    if not isinstance(manifest, ImageEvidenceManifest) or manifest._question_sha256 != _question_digest(question):
        return None, "图像依据不属于本次分类请求"
    if not isinstance(proof, dict) or set(proof) != {"image_id", "sha256", "transcription", "step_quote"}:
        return None, "图片解析证据字段不完整"
    binding = next((item for item in manifest._bindings if item.image_id == proof["image_id"]), None)
    if binding is None or proof["sha256"] != binding.sent_sha256:
        return None, "图片未在本次请求发送或图像摘要不符"
    field, evidence = point.get("evidence_source"), point.get("evidence")
    if not isinstance(field, str) or field not in {"answer_markdown", "review_markdown"} or (field, evidence) not in binding.references:
        return None, "图片依据必须逐字引用所属解析或评析字段的原图引用"
    if not isinstance(question.get(field), str) or evidence not in question[field]:
        return None, "解析图片引用已变化"
    if not image_reference_is_actual(question[field], evidence):
        return None, "图片位于假设、替代方法或否定说明中，不能作为实际原解"
    transcription, quote = proof["transcription"], proof["step_quote"]
    if (not isinstance(transcription, str) or not transcription.strip() or "\x00" in transcription
            or len(transcription) > MAX_IMAGE_TRANSCRIPTION or not isinstance(quote, str)
            or not quote.strip() or "\x00" in quote or len(quote) > MAX_IMAGE_STEP_QUOTE):
        return None, "图片转录或步骤引文为空、格式无效或过长"
    if (not solution_context({field: transcription})["has_reasoning"]
            or not evidence_is_reasoning(transcription, quote)):
        return None, "图片转录未显示可核对的实际步骤，只有答案或假设方法不能采用"
    try:
        if (binding.file_path.is_symlink() or binding.file_path.resolve() != binding.file_path
                or not binding.file_path.is_file()):
            return None, "分类时发送的解析图片已不可用"
        with binding.file_path.open("rb") as stream:
            current = read_stream_limited(stream, binding.max_bytes)
        if hashlib.sha256(current).hexdigest() != binding.raw_sha256:
            return None, "分类期间解析图片内容已变化"
    except (OSError, ValueError):
        return None, "分类时发送的解析图片已不可用"
    return {"image_id": binding.image_id, "sha256": binding.sent_sha256, "field": field,
            "basis": "model_image_transcription_not_independently_verified",
            "transcription_sha256": _sha(transcription), "transcription_length": len(transcription),
            "step_quote": quote}, None


def image_step_reason(reason, proof):
    # Keep the source type and an exact, bounded step quote in existing metadata.
    prefix = f"{IMAGE_REASON_NOTICE}；步骤引文：{proof['step_quote']}。"
    return prefix + reason[:max(0, 1000 - len(prefix))]
