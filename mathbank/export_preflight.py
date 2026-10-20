"""Fail-closed, local-only image checks for generated paper exports.

Reports contain hashes and resource locations, never question text or secrets.
They describe an immutable export snapshot, not a certification of later edits.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime, timezone
from uuid import uuid4

from .asset_security import AssetSecurityError, SAFE_IMAGE_EXTENSIONS, resolve_upload_asset
from .content_locks import _reference_literal_ranges
from .paths import EXPORT_PREFLIGHTS_DIR
from .question_assets import image_reference_spans

MAX_REPORTS = 100
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_REFERENCES = 2000
_REPORT_LOCK = threading.Lock()
_IMAGE = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
_MARKER = re.compile(r"^% MathBank-Question-ID: ([0-9]+)\s*$", re.MULTILINE)
_SAFE_TEX_IMAGE = re.compile(r"(?:(?:images/|\./))?([A-Za-z0-9_.-]+)\Z")
# These upload formats are also natively supported by XeLaTeX/graphicx. Do not
# glob the upload directory or infer vector/document types for an omitted suffix.
_TEX_INFERRED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})


class ExportPreflightError(ValueError):
    def __init__(self, report: dict):
        self.report = report
        items = report.get("errors", [])
        details = "；".join(
            f"题 #{item['question_id']}（{item['field']}）：{item['reference']}"
            for item in items[:4]
        )
        super().__init__("导出预检未通过，未生成完整试卷。" + details)


def _digest(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def tex_image_references(source: str):
    """Yield actual graphics commands, including those inside executable TikZ.

    Keep source offsets so question markers stay associated with the generated
    output. Comments, verbatim examples and literal macros cannot require files.
    """
    chars = list(source)
    ranges = _reference_literal_ranges(source, tex_comments=False)
    graphics_arguments = [match.span(1) for match in _IMAGE.finditer(source)]
    for start, end in ranges:
        if source.startswith(r"\begin{tikzpicture}", start):
            continue
        # A URL used as an actual graphics argument must be rejected by the
        # path checks, rather than masking its closing brace as prose. Outer
        # code/verbatim/comment ranges still mask literal command examples.
        if (source[start:end].lower().startswith(("https://", "http://"))
                and any(left <= start < right for left, right in graphics_arguments)):
            continue
        chars[start:end] = ["\n" if char == "\n" else " " for char in source[start:end]]
    masked = "".join(chars)
    for comment in re.finditer(r"%[^\n]*", masked):
        preceding = comment.start() - 1
        while preceding >= 0 and masked[preceding] == "\\":
            preceding -= 1
        if (comment.start() - preceding - 1) % 2 == 0:
            chars[comment.start():comment.end()] = [" "] * len(comment.group())
    masked = "".join(chars)
    markers = list(_MARKER.finditer(source))
    marker_index, question_id = 0, None
    for match in _IMAGE.finditer(masked):
        while marker_index < len(markers) and markers[marker_index].start() < match.start():
            question_id = int(markers[marker_index].group(1))
            marker_index += 1
        # An escaped command is visible literal text, not a graphics operation.
        preceding = match.start() - 1
        while preceding >= 0 and masked[preceding] == "\\":
            preceding -= 1
        if (match.start() - preceding - 1) % 2:
            continue
        yield question_id, match.group(1).strip()


def _display_reference(reference: str) -> str:
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", reference) or "?" in reference or "#" in reference:
        return "[外部或带参数图片路径]"
    return reference[:240]


def _resolve_tex_image_name(reference: str, bound_names) -> str:
    """Resolve graphicx's omitted suffix only against bound raster resources.

    An explicitly bound filename wins. Otherwise exactly one safe raster may
    extend the authored name (including a name containing dots). Both preflight
    and the final copy/package guard use this rule; unrelated on-disk files
    cannot supply or disambiguate a question's image.
    """
    match = _SAFE_TEX_IMAGE.fullmatch(reference)
    if match is None:
        raise AssetSecurityError("unsupported_export_path")
    basename = match.group(1)
    # Raw \includegraphics{diagram} text is a reference, not itself a binding
    # to an extensionless upload file. Only actual supported asset names count.
    names = {name for name in bound_names if Path(name).suffix.lower() in SAFE_IMAGE_EXTENSIONS}
    if basename in names:
        return basename
    matches = {name for name in names
               if _SAFE_TEX_IMAGE.fullmatch(name)
               and Path(name).suffix.lower() in _TEX_INFERRED_IMAGE_EXTENSIONS
               and Path(name).stem == basename}
    if len(matches) > 1:
        raise AssetSecurityError("ambiguous_image_reference")
    return next(iter(matches), basename)


def _write_report(report: dict, directory: Path | None = None) -> None:
    root = Path(directory) if directory is not None else Path(EXPORT_PREFLIGHTS_DIR)
    data = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > MAX_REPORT_BYTES:
        raise OSError("导出预检报告超过安全上限。")
    with _REPORT_LOCK:
        root.mkdir(parents=True, exist_ok=True)
        reports = sorted((item for item in root.glob("*.json")
                          if re.fullmatch(r"[0-9a-f]{32}\.json", item.name)),
                         key=lambda item: item.lstat().st_mtime_ns, reverse=True)
        # Make room before writing. A failed prune cannot grow the directory
        # on every subsequent export or silently remove unrelated JSON files.
        for old in reports[MAX_REPORTS - 1:]:
            old.unlink()
        fd, temporary = tempfile.mkstemp(prefix=".preflight-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, root / (report["id"] + ".json"))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)



def read_export_preflight(report_id: str, *, directory: Path | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", report_id):
        raise FileNotFoundError("导出预检记录不存在或已过期。")
    root = Path(directory) if directory is not None else Path(EXPORT_PREFLIGHTS_DIR)
    path = root / (report_id + ".json")
    if path.is_symlink() or path.stat().st_size > MAX_REPORT_BYTES:
        raise FileNotFoundError("导出预检记录无效。")
    return json.loads(path.read_text(encoding="utf-8"))


def preflight_paper_export(questions_data: list, sources: dict[str, str], *, target: str,
                           uploads_dir, upload_url_prefix="static/uploads", directory=None):
    """Persist a report and return (report, actual export files), or block.

    A missing preview PNG replaced by an editable TikZ source is intentionally
    absent from the final graphics commands and does not block vector export.
    Unused review/reference-only assets likewise do not enter a paper package.
    """
    questions = {item["question"].get("id"): item["question"] for item in questions_data}
    owners = {}
    for question_id, question in questions.items():
        embedded = set()
        for field in ("content", "answer_markdown"):
            for _, _, reference in image_reference_spans(question.get(field, ""), local_only=False):
                owners.setdefault(Path(reference).name, []).append((question_id, field, reference))
                embedded.add(reference.lstrip("/"))
        for reference in question.get("image_paths", []) or []:
            if isinstance(reference, str) and reference.lstrip("/") not in embedded:
                owners.setdefault(Path(reference).name, []).append((question_id, "image_paths", reference))
    report = {
        "id": uuid4().hex, "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "target": target,
        "status": "passed", "question_sha256": {str(key): _digest(q) for key, q in questions.items()},
        "source_sha256": {name: hashlib.sha256(source.encode("utf-8")).hexdigest()
                          for name, source in sources.items() if source},
        "resources": [], "errors": [],
    }
    paths, seen, consumed = [], set(), []
    checked = {}
    for source_name, source in sources.items():
        if not source:
            continue
        for marker_id, reference in tex_image_references(source):
            if len(seen) >= MAX_IMAGE_REFERENCES:
                report["errors"].append({"question_id": marker_id, "field": "generated_tex",
                                         "reference": "[图片引用超限]", "reason": "reference_limit"})
                break
            consumed.append((source_name, marker_id, reference))
            bound_names = (name for name, entries in owners.items()
                           if marker_id not in questions or any(owner[0] == marker_id for owner in entries))
            resolution_error = None
            try:
                basename = _resolve_tex_image_name(reference, bound_names)
            except AssetSecurityError as exc:
                basename = Path(reference).name
                resolution_error = exc
            candidates = owners.get(basename, [])
            if marker_id in questions:
                candidates = [owner for owner in candidates if owner[0] == marker_id] or [(marker_id, "generated_tex", basename)]
            if not candidates:
                candidates = [(None, "generated_tex", basename)]
            for question_id, field, original in dict.fromkeys(candidates):
                identity = (question_id, field, original, reference)
                if identity in seen:
                    continue
                seen.add(identity)
                entry = {"question_id": question_id, "field": field,
                         "reference": _display_reference(original), "source": source_name}
                try:
                    if resolution_error is not None:
                        raise resolution_error
                    # Only a file actually consumed by generated TeX can
                    # use the existing verified retention recovery path.
                    original_match = _SAFE_TEX_IMAGE.fullmatch(original)
                    asset_reference = original_match.group(1) if original_match else original
                    path = resolve_upload_asset(asset_reference, uploads_dir=uploads_dir,
                                                url_prefix=upload_url_prefix, require_file=False)
                    restored = not path.is_file()
                    if restored:
                        path = resolve_upload_asset(asset_reference, uploads_dir=uploads_dir,
                                                    url_prefix=upload_url_prefix, require_file=True)
                    if not path.is_file():
                        raise FileNotFoundError("missing_file")
                    file_key = basename.casefold()
                    if file_key in checked and checked[file_key][0] != path:
                        raise AssetSecurityError("basename_collision")
                    if file_key not in checked:
                        size, digest = _file_digest(path)
                        checked[file_key] = (path, size, digest, restored)
                        paths.append(str(path))
                    _, size, digest, restored = checked[file_key]
                    entry.update(status="available", bytes=size, sha256=digest, restored=restored)
                except (OSError, AssetSecurityError, ValueError, TypeError) as exc:
                    reason = str(exc) if str(exc) in {"unsupported_export_path", "basename_collision", "missing_file", "ambiguous_image_reference"} else "unreadable_or_unsafe"
                    entry.update(status="blocked", reason=reason)
                    report["errors"].append(dict(entry))
                report["resources"].append(entry)
    # Two questions can each bind a different suffix of the same authored
    # name. Once both files enter one package, graphicx could select the wrong
    # one; reject that ambiguity before returning a successful preflight.
    if not report["errors"]:
        for source_name, question_id, reference in dict.fromkeys(consumed):
            try:
                _resolve_tex_image_name(reference, (Path(path).name for path in paths))
            except AssetSecurityError as exc:
                entry = {"question_id": question_id, "field": "generated_tex",
                         "reference": _display_reference(reference), "source": source_name,
                         "status": "blocked", "reason": str(exc)}
                report["errors"].append(entry)
                report["resources"].append(dict(entry))
    report["status"] = "blocked" if report["errors"] else "passed"
    report["counts"] = {"questions": len(questions), "resources": len(report["resources"]),
                        "errors": len(report["errors"])}
    _write_report(report, directory)
    if report["errors"]:
        raise ExportPreflightError(report)
    return report, paths


def assert_tex_image_files(sources: list[str], image_paths: list) -> list:
    """Recheck at the copy/package boundary; never silently skip a missing file."""
    available = {}
    for item in image_paths:
        path = Path(item)
        if path.is_symlink() or not path.is_file():
            raise ValueError("导出图片已丢失或不可读：" + path.name)
        if path.name.casefold() in available and available[path.name.casefold()] != path:
            raise ValueError("导出图片文件名冲突：" + path.name)
        available[path.name.casefold()] = path
    for source in sources:
        for _, reference in tex_image_references(source or ""):
            try:
                basename = _resolve_tex_image_name(reference, (path.name for path in available.values()))
            except AssetSecurityError as exc:
                if str(exc) == "ambiguous_image_reference":
                    raise ValueError("导出图片引用存在歧义：" + _display_reference(reference)) from exc
                raise ValueError("导出源码存在未打包图片：" + _display_reference(reference)) from exc
            if basename.casefold() not in available:
                raise ValueError("导出源码存在未打包图片：" + _display_reference(reference))
    return list(available.values())
