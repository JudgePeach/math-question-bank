"""Bounded, task-owned candidate image evidence for final source review.

Only images actually referenced in candidate text are read. A caller-supplied
task allowlist and the shared upload resolver are both required; stored library
assets, hidden reference images and external URLs are never evidence here.
"""

from __future__ import annotations

import base64
from collections import Counter
import hashlib
import io
import json
import os
import re
import warnings
from typing import Iterable

from PIL import Image

from mathbank.asset_security import AssetSecurityError, resolve_upload_asset
from mathbank.paths import TEST_UPLOADS_DIR, UPLOADS_DIR


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
# Small formula previews should be limited by their actual resources, not a
# six-image threshold. Counts remain hard safety ceilings for adversarial
# inputs; complete request text is separately capped by each verifier.
MAX_ITEM_IMAGES = 64
MAX_ITEM_REFERENCES = 64
MAX_ITEM_BYTES = 12 * 1024 * 1024
MAX_ITEM_PIXELS = 8_000_000
MAX_ITEM_LABEL_CHARS = 12_000
MAX_BATCH_IMAGES = 96
MAX_BATCH_BYTES = 24 * 1024 * 1024
MAX_BATCH_PIXELS = 16_000_000
MAX_BATCH_LABEL_CHARS = 18_000
MAX_FIELD_CHARS = 50_000
_FIELDS = ("content", "answer_markdown")
_FORMATS = {"PNG": ("image/png", {".png"}), "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
            "WEBP": ("image/webp", {".webp"}), "GIF": ("image/gif", {".gif"})}
_IMAGE_START = re.compile(r"!\[|\\includegraphics\b|<\s*img\b", re.IGNORECASE)
_MARKDOWN = re.compile(r"!\[(?:\\.|[^\]\\\n])*\]\(([^\n)]*)\)")
_LATEX = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]\n]*\]\s*)?\{([^{}\n]*)\}")
_DESTINATION = re.compile(r'''(?:<([^<>\n]+)>|([^\s<>]+))(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'))?''')
_LITERAL_START = re.compile(
    r"<!--|^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$|`+|"
    r"\\begin[ \t]*\{(verbatim\*?|Verbatim\*?|lstlisting|minted)\}|"
    r"\\(?:verb|Verb|lstinline|mintinline|detokenize|url|path)(?![A-Za-z])\*?", re.MULTILINE)
_REASONS = {
    "syntax": "候选图片引用格式无法完整识别。",
    "field": "候选题文类型或长度不符合图片核验范围。",
    "item_limit": "单题候选图片或引用次数超过自动核验安全上限。",
    "item_resources": "单题候选图片的总大小、总像素数或标签文字超过自动核验资源上限。",
    "batch_limit": "候选图片超过本批自动核验数量、总大小、总像素数或标签文字上限。",
    "ownership": "候选图片不属于本次导入的临时资源。",
    "asset": "候选图片路径不安全、文件缺失或格式不受支持。",
    "size": "候选图片大小或像素数超过自动核验上限。",
    "content": "候选图片损坏、含多帧或实际格式与扩展名不符。",
    "changed": "读取期间候选图片发生变化，未作为核验依据。",
}


class _ImageEvidenceError(ValueError):
    pass


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _escaped(value: str, start: int) -> bool:
    preceding = start - 1
    while preceding >= 0 and value[preceding] == "\\":
        preceding -= 1
    return bool((start - preceding - 1) % 2)


def _brace_end(value: str, start: int) -> int:
    depth = 0
    for index in range(start, len(value)):
        if value[index] not in "{}" or _escaped(value, index):
            continue
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(value)


def _visible_text(value: str) -> str:
    """Mask supported literal syntax without changing offsets or line breaks.

    This deliberately recognizes only explicit code/comment constructs and a
    small list of TeX literal macros; unknown macro arguments remain visible.
    Processing in reading order prevents syntax inside one literal from hiding
    unrelated content after it. Callers may also use the preserved offsets to
    distinguish visible diagnostic markers from quoted examples.
    """
    chars, cursor = list(value), 0
    while match := _LITERAL_START.search(value, cursor):
        token, end = match.group(), match.end()
        if _escaped(value, match.start()):
            cursor = end
            continue
        if token == "<!--":
            closing = value.find("-->", end)
            end = closing + 3 if closing >= 0 else len(value)
        elif match.group(1):
            fence = match.group(1)
            closing = re.search(r"^[ \t]{0,3}" + re.escape(fence[0]) + "{" + str(len(fence))
                                + r",}[ \t]*$", value[end:], re.MULTILINE)
            end = end + closing.end() if closing else len(value)
        elif token.startswith("`"):
            closing = next((part for part in re.finditer(r"`+", value[end:])
                            if len(part.group()) == len(token)), None)
            if closing is None:
                cursor = end
                continue
            end += closing.end()
        elif match.group(2):
            environment = match.group(2)
            closing = re.search(r"\\end[ \t]*\{" + re.escape(environment) + r"\}", value[end:])
            end = end + closing.end() if closing else len(value)
        else:
            command = token.lstrip("\\").rstrip("*")
            argument = end
            while argument < len(value) and value[argument] in " \t":
                argument += 1
            if command in {"Verb", "lstinline", "mintinline"} and value[argument:argument + 1] == "[":
                option_end = value.find("]", argument + 1)
                if option_end < 0:
                    option_end = len(value) - 1
                argument = option_end + 1
                while argument < len(value) and value[argument] in " \t":
                    argument += 1
            if command == "mintinline" and value[argument:argument + 1] == "{":
                # Minted has a required language group before its braced or
                # delimiter-form literal body. Language is never image text.
                argument = _brace_end(value, argument)
                while argument < len(value) and value[argument] in " \t":
                    argument += 1
            elif command == "mintinline" and argument < len(value):
                cursor = end
                continue
            if argument >= len(value):
                end = len(value)
            elif value[argument] in "\r\n":
                cursor = end
                continue
            elif value[argument] == "{" and command not in {"verb", "Verb"}:
                end = _brace_end(value, argument)
            elif command != "detokenize" and not value[argument].isalnum() and value[argument] not in " \t":
                line_end = value.find("\n", argument + 1)
                line_end = len(value) if line_end < 0 else line_end
                closing = value.find(value[argument], argument + 1, line_end)
                end = closing + 1 if closing >= 0 else line_end
            else:
                cursor = end
                continue
        for index in range(match.start(), end):
            if chars[index] not in "\r\n":
                chars[index] = " "
        cursor = end
    return "".join(chars)


def _references(value: str, identifier: str, field: str) -> list[dict]:
    visible, refs, cursor = _visible_text(value), [], 0
    for start in _IMAGE_START.finditer(visible):
        if start.start() < cursor:
            continue
        if _escaped(visible, start.start()):
            continue
        match = (_MARKDOWN if start.group() == "![" else _LATEX).match(visible, start.start())
        path = ""
        if match:
            cursor = match.end()
            target = match.group(1).strip()
            if start.group() == "![":
                destination = _DESTINATION.fullmatch(target)
                if destination:
                    path = destination.group(1) or destination.group(2)
            else:
                path = target
        refs.append({"id": identifier, "field": field, "occurrence": len(refs) + 1, "path": path})
        if len(refs) > MAX_ITEM_REFERENCES:
            break
    return refs


def _stat_key(status) -> tuple:
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns


def _read_image(reference: str, allowed: set[str], uploads_dir, test_uploads_dir) -> tuple[dict, bytes]:
    if reference not in allowed:
        raise _ImageEvidenceError("ownership")
    if reference.startswith("/static/uploads/tmp/"):
        root, prefix = uploads_dir, "/static/uploads"
    elif reference.startswith("/static/test_uploads/tmp/"):
        root, prefix = test_uploads_dir, "/static/test_uploads"
    else:
        raise _ImageEvidenceError("ownership")
    try:
        path = resolve_upload_asset(reference, uploads_dir=root, url_prefix=prefix)
        before = path.stat()
        if not 0 < before.st_size <= MAX_IMAGE_BYTES:
            raise _ImageEvidenceError("size")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            stream = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            if _stat_key(os.fstat(stream.fileno())) != _stat_key(before):
                raise _ImageEvidenceError("changed")
            data = stream.read(MAX_IMAGE_BYTES + 1)
            after_read = os.fstat(stream.fileno())
        checked_path = resolve_upload_asset(reference, uploads_dir=root, url_prefix=prefix)
        if (checked_path != path or _stat_key(before) != _stat_key(after_read)
                or _stat_key(before) != _stat_key(checked_path.stat()) or len(data) != before.st_size):
            raise _ImageEvidenceError("changed")
    except (AssetSecurityError, OSError):
        raise _ImageEvidenceError("asset") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                width, height = picture.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise _ImageEvidenceError("size")
                if (picture.format not in _FORMATS or path.suffix.lower() not in _FORMATS[picture.format][1]
                        or getattr(picture, "n_frames", 1) != 1):
                    raise _ImageEvidenceError("content")
                media_type = _FORMATS[picture.format][0]
                picture.verify()
            # verify() checks container integrity; load() also checks decoding.
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
                rgba = picture.convert("RGBA")
                flattened = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
                is_uniform = all(low == high for low, high in flattened.getextrema())
    except _ImageEvidenceError:
        raise
    except Exception:
        raise _ImageEvidenceError("content") from None
    return {"path": reference, "sha256": hashlib.sha256(data).hexdigest(), "width": width,
            "height": height, "media_type": media_type, "byte_size": len(data), "is_uniform": is_uniform}, data


def _image_label(image_id, path, bindings):
    label = {"candidate_image": image_id, "path": path, "bindings": bindings}
    return "候选题文实际图片（不是原页图）：" + json.dumps(label, ensure_ascii=False)


def prepare_candidate_images(items: list[dict], *, allowed_paths: Iterable[str],
                             uploads_dir=UPLOADS_DIR, test_uploads_dir=TEST_UPLOADS_DIR) -> dict:
    """Return multimodal parts, per-item completeness and an evidence fingerprint.

    Input items have unique string ``id`` and an ``output`` mapping containing
    ``content``/``answer_markdown``. Images are deduplicated by their exact URL;
    each one retains every item/field/one-based occurrence binding. Failures are
    local to affected items. An incomplete item must never be certified, even
    if the model returns an equivalent verdict. Calling again after a request
    and comparing fingerprints detects image changes as well as rebinding.
    """
    identifiers = [item.get("id") if isinstance(item, dict) else None for item in items]
    if (any(not isinstance(identifier, str) or not identifier for identifier in identifiers)
            or any(count != 1 for count in Counter(identifiers).values())):
        raise ValueError("Candidate image evidence requires unique nonempty string IDs")
    allowed = {path for path in allowed_paths if isinstance(path, str)}
    per_item, references, all_bindings, field_hashes = {}, {}, {}, {}
    images, image_data = {}, {}
    total_bytes, total_pixels, total_label_chars = 0, 0, 0
    for item in items:
        identifier, output = item["id"], item.get("output")
        result = per_item[identifier] = {"images": [], "complete": True, "reasons": []}
        refs = references[identifier] = []
        field_hashes[identifier] = {}
        for field in _FIELDS:
            value = output.get(field, "") if isinstance(output, dict) else None
            if not isinstance(value, str) or len(value) > MAX_FIELD_CHARS:
                result["reasons"].append(_REASONS["field"])
                continue
            field_hashes[identifier][field] = hashlib.sha256(value.encode("utf-8")).hexdigest()
            refs.extend(_references(value, identifier, field))
        if any(not reference["path"] for reference in refs):
            result["reasons"].append(_REASONS["syntax"])
        paths = list(dict.fromkeys(reference["path"] for reference in refs if reference["path"]))
        if len(paths) > MAX_ITEM_IMAGES or len(refs) > MAX_ITEM_REFERENCES:
            result["reasons"].append(_REASONS["item_limit"])
            continue
        if result["reasons"]:
            continue
        item_bindings = {path: [{key: value for key, value in ref.items() if key != "path"}
                                for ref in refs if ref["path"] == path] for path in paths}
        # Admit a whole question atomically: a large/invalid question must not
        # consume the next question's budget or leak a partial image group.
        pending_images, pending_data = {}, {}
        item_bytes = item_pixels = added_bytes = added_pixels = 0
        try:
            new_paths = [path for path in paths if path not in images]
            image_ids = {path: images[path]["image_id"] for path in paths if path in images}
            image_ids.update({path: f"candidate_image_{len(images) + index + 1:03d}"
                              for index, path in enumerate(new_paths)})
            item_label_chars = sum(len(_image_label(image_ids[path], path, item_bindings[path])) for path in paths)
            if item_label_chars > MAX_ITEM_LABEL_CHARS:
                raise _ImageEvidenceError("item_resources")
            added_label_chars = sum(
                len(_image_label(image_ids[path], path, [*all_bindings.get(path, []), *item_bindings[path]]))
                - (len(_image_label(image_ids[path], path, all_bindings[path])) if path in all_bindings else 0)
                for path in paths)
            if (len(images) + len(new_paths) > MAX_BATCH_IMAGES
                    or total_label_chars + added_label_chars > MAX_BATCH_LABEL_CHARS):
                raise _ImageEvidenceError("batch_limit")
            for path in paths:
                if path in images:
                    metadata = images[path]
                else:
                    metadata, data = _read_image(path, allowed, uploads_dir, test_uploads_dir)
                pixels = metadata["width"] * metadata["height"]
                item_bytes += metadata["byte_size"]
                item_pixels += pixels
                if item_bytes > MAX_ITEM_BYTES or item_pixels > MAX_ITEM_PIXELS:
                    raise _ImageEvidenceError("item_resources")
                if path not in images:
                    added_bytes += metadata["byte_size"]
                    added_pixels += pixels
                    if total_bytes + added_bytes > MAX_BATCH_BYTES or total_pixels + added_pixels > MAX_BATCH_PIXELS:
                        raise _ImageEvidenceError("batch_limit")
                    pending_images[path] = {"image_id": image_ids[path], **metadata}
                    pending_data[path] = data
        except _ImageEvidenceError as exc:
            result["reasons"].append(_REASONS[str(exc)])
            continue
        images.update(pending_images)
        image_data.update(pending_data)
        total_bytes += added_bytes
        total_pixels += added_pixels
        total_label_chars += added_label_chars
        for path in paths:
            all_bindings.setdefault(path, []).extend(item_bindings[path])
            result["images"].append({**images[path], "bindings": item_bindings[path]})
    messages = []
    for path, metadata in images.items():
        messages.extend([{"type": "text", "text": _image_label(metadata["image_id"], path, all_bindings[path])},
                         {"type": "image_url", "image_url": {"url": f"data:{metadata['media_type']};base64,"
                                                              + base64.b64encode(image_data[path]).decode("ascii")}}])
    for result in per_item.values():
        result["reasons"] = list(dict.fromkeys(result["reasons"]))
        result["complete"] = not result["reasons"]
    fingerprint = _digest({"references": references, "per_item": per_item, "field_hashes": field_hashes})
    return {"per_item": per_item, "messages": messages, "fingerprint": fingerprint}
