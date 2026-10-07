"""Private PDF native proofs, independent of OCR and public completeness flags.

The native producer examines original immutable PDF bytes once. Later binding
and verification preserve page source offsets and assets without reopening the
PDF for every question group. Unsupported mathematics stays explicitly risky.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import secrets

import pymupdf as fitz


MAX_PAGES = 100
MAX_SOURCE_CHARACTERS = 500_000
MAX_GLYPHS = 10_000
_SECRET = secrets.token_bytes(32)
_PAGE_MARKER = re.compile(r"<!-- MATHBANK_PDF_PAGE:(\d+) -->")
_BUILTIN_FONTS = {"Helvetica": "helv", "Times-Roman": "tiro", "Courier": "cour",
                  "Heiti": "china-s", "Song": "china-ss"}
_DIAGNOSTIC_KEYS = ("pdf_native_repair", "pdf_native_regions", "pdf_layout")


@dataclass(frozen=True)
class _NativePdfEvidence:
    payload: str
    signature: str


@dataclass(frozen=True)
class _PdfSourceReviewEvidence:
    payload: str
    signature: str


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _seal(kind, payload):
    text = _json(payload)
    return kind(text, hmac.new(_SECRET, text.encode(), hashlib.sha256).hexdigest())


def _open(kind, evidence):
    if (type(evidence) is not kind or not isinstance(evidence.payload, str)
            or not isinstance(evidence.signature, str)):
        return None
    signature = hmac.new(_SECRET, evidence.payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, evidence.signature):
        return None
    try:
        return json.loads(evidence.payload)
    except (TypeError, ValueError):
        return None


def _font_name(value):
    return re.sub(r"^[A-Z]{6}\+", "", value).removesuffix("-Identity-H")


def _finite(values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def _plain_character(value):
    cp = ord(value)
    return (value.isspace() or "0" <= value <= "9" or 0x3400 <= cp <= 0x9FFF
            or value in ".，。；：！？、（）()[]【】“”‘’…—-·")


def _plain_text_proof(page, markdown, font_cache):
    """Only literal Chinese/digit prose with independent glyph-ID coverage."""
    raw = page.get_text("rawdict")
    blocks = raw.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return None, "native_glyph_evidence_missing", True
    lines, chars = [], []
    for block in blocks:
        if block.get("type", 0) != 0 or not block.get("lines"):
            return None, "native_nontext_or_missing_lines", True
        for line in block["lines"]:
            if line.get("wmode", 0) or tuple(line.get("dir", (1, 0))) != (1, 0) or not line.get("spans"):
                return None, "native_reading_order_unproved", True
            current = []
            for span in line["spans"]:
                font = _font_name(str(span.get("font", "")))
                if (not font or any(s in font.lower() for s in ("math", "symbol", "cmmi", "cmsy", "cmex"))
                        or span.get("alpha", 255) != 255 or not span.get("chars")):
                    return None, "unrepaired_math_font_or_visibility", False
                for char in span["chars"]:
                    value, origin, box = char.get("c"), char.get("origin"), char.get("bbox")
                    if (not isinstance(value, str) or len(value) != 1 or not _plain_character(value)
                            or not origin or not box or not _finite([*origin, *box])
                            or box[2] <= box[0] or box[3] <= box[1]):
                        return None, "unrepaired_math_or_unknown_glyph", False
                    if not page.rect.contains(fitz.Rect(box)):
                        return None, "native_clipped_glyph", True
                    current.append((value, origin, box, font))
                    chars.append((value, origin, box, font))
            if not current or len(chars) > MAX_GLYPHS:
                return None, "native_glyph_budget_or_empty_line", True
            if any(a[1][0] > b[1][0] + .1 for a, b in zip(current, current[1:])):
                return None, "native_reading_order_unproved", True
            lines.append(current)
    baselines = [sum(c[1][1] for c in line) / len(line) for line in lines]
    if any(b <= a + 1 for a, b in zip(baselines, baselines[1:])):
        return None, "native_multicolumn_or_nonmonotonic_order", True
    expected = "\n".join("".join(c[0] for c in line) for line in lines).strip()
    if markdown.strip() != expected:
        return None, "native_text_not_exact_physical_glyph_sequence", False
    records = page.get_fonts(full=True)
    trace = page.get_texttrace()
    wanted = Counter((ord(c), round(o[0], 3), round(o[1], 3), f) for c, o, _, f in chars)
    seen = Counter()
    support = []
    for span in trace:
        name = _font_name(str(span.get("font", "")))
        if span.get("type") != 0 or span.get("opacity", 1) != 1:
            return None, "native_invisible_or_stroked_text", True
        matches = [r for r in records if _font_name(str(r[3])) == name]
        if len(matches) != 1:
            return None, "native_font_mapping_not_unique", False
        key = (matches[0][0], name)
        if key not in font_cache:
            font_bytes = page.parent.extract_font(matches[0][0])[-1]
            if font_bytes:
                font = fitz.Font(fontbuffer=font_bytes)
            elif name in _BUILTIN_FONTS:
                font = fitz.Font(fontname=_BUILTIN_FONTS[name])
            else:
                return None, "native_font_unicode_mapping_unproved", False
            font_cache[key] = font
        font = font_cache[key]
        for cp, gid, origin, box in span.get("chars", []):
            if (type(cp) is not int or type(gid) is not int or gid <= 0
                    or font.has_glyph(cp, fallback=False) != gid):
                return None, "native_font_glyph_identity_unproved", False
            seen[(cp, round(origin[0], 3), round(origin[1], 3), name)] += 1
        support.append(name)
    if seen != wanted or any(amount != 1 for amount in wanted.values()):
        return None, "native_visible_glyph_coverage_not_exact", True
    return {"total": len(chars), "consumed": len(chars), "fonts": sorted(set(support)),
            "kind": "literal_chinese_and_digits", "glyph_sequence_sha256": _sha(_json(chars))}, "", False


def _physical_assessment(page, row, font_cache):
    from mathbank.pdf_native_math import repair_native_page
    from mathbank.pdf_inspector_helper import _simple_paint_resources, _native_paint_visibility_reasons
    report = {"page_index": page.number, "page_number": page.number + 1, "rotation": page.rotation,
        "reliable": False, "reasons": [], "global_reasons": [], "glyph_summary": {},
        "reading_order": {"status": "unproved"}, "figure_ownership": {"status": "unproved"},
        "native_markdown_sha256": _sha(str(row.get("markdown") or "").strip()),
        "native_origin": str(row.get("source") or ""), "width": page.rect.width, "height": page.rect.height}
    def reject(reason, global_=False):
        report["reasons"].append(reason)
        if global_: report["global_reasons"].append(reason)
        return report
    if page.rotation:
        return reject("native_rotation_unsupported", True)
    if not _simple_paint_resources(page):
        return reject("native_visibility_resources_unproved", True)
    if _native_paint_visibility_reasons(str(row.get("markdown") or ""), page):
        return reject("native_paint_visibility_unproved", True)
    if page.get_image_info():
        return reject("native_bitmap_content_or_figure_ownership_unproved", True)
    markdown = str(row.get("markdown") or "").strip()
    if row.get("source") == "native-math-repaired":
        repair = repair_native_page(page)
        stats = repair.get("stats", {})
        if (repair.get("status") != "repaired" or repair.get("markdown", "").strip() != markdown
                or not stats.get("glyphs_total") or stats.get("glyphs_consumed") != stats.get("glyphs_total")
                or stats.get("drawings_consumed") != stats.get("drawings_total")):
            return reject("native_repair_not_reproduced_completely")
        report.update(reliable=True, glyph_summary={**stats, "kind": "completed_native_math_repair"},
            reading_order={"status": "proved", "method": "bounded_original_glyph_reconstruction"},
            figure_ownership={"status": "proved", "method": "all_vectors_consumed_as_supported_math_or_fillins"},
            repair_sha256=_sha(_json(repair)))
        return report
    if row.get("source") not in {"pdf-inspector", "pymupdf"}:
        return reject("native_origin_not_individually_attested", True)
    if page.get_drawings():
        return reject("native_vector_or_figure_ownership_unproved", True)
    proof, reason, global_ = _plain_text_proof(page, markdown, font_cache)
    if proof is None:
        return reject(reason, global_)
    report.update(reliable=True, glyph_summary=proof,
        reading_order={"status": "proved", "method": "exact_single_column_original_glyph_sequence"},
        figure_ownership={"status": "proved", "method": "original_page_has_no_images_or_vectors"})
    return report


def _capture_native_source_evidence(file_bytes, pages, page_indices=None):
    """Called only by the native inspector before any model page results."""
    data = bytes(file_bytes)
    reasons, reports = [], []
    with fitz.open(stream=data, filetype="pdf") as document:
        selected = list(page_indices) if page_indices is not None else list(range(len(document)))
        actual = [row.get("page_index") for row in pages]
        if (len(selected) > MAX_PAGES or selected != sorted(set(selected)) or actual != selected
                or any(type(i) is not int or not 0 <= i < len(document) for i in selected)):
            reasons.append("original_page_coverage_or_order_unproved")
        fonts = {}
        for row in pages[:MAX_PAGES]:
            index = row.get("page_index")
            if type(index) is not int or not 0 <= index < len(document):
                reasons.append("original_page_identity_unproved")
                continue
            try:
                reports.append(_physical_assessment(document[index], row, fonts))
            except Exception:
                reports.append({"page_index": index, "page_number": index + 1, "reliable": False,
                    "reasons": ["native_physical_analysis_unavailable"], "global_reasons": ["native_physical_analysis_unavailable"],
                    "glyph_summary": {}, "reading_order": {"status": "unproved"}, "figure_ownership": {"status": "unproved"},
                    "native_markdown_sha256": _sha(str(row.get("markdown") or "").strip()), "native_origin": str(row.get("source") or "")})
    return _seal(_NativePdfEvidence, {"source_document_sha256": _sha(data), "pages": reports,
        "selected_page_indices": selected, "global_reasons": reasons, "analysis": {"pdf_opens": 1, "pages_examined": len(reports)}})


def _diagnostic_hash(diagnostics):
    if not isinstance(diagnostics, dict):
        raise ValueError("native_diagnostics_invalid")
    return _sha(_json({key: diagnostics.get(key) for key in _DIAGNOSTIC_KEYS}))


def _page_chunks(source_pages):
    chunks, reasons, numbers = [], [], []
    for row in source_pages:
        number = row.get("page_number")
        if type(number) is not int or number <= 0 or number in numbers:
            reasons.append("source_page_identity_unproved")
        numbers.append(number)
        text = str(row.get("markdown", row.get("text", "")) or "").strip()
        markers = list(_PAGE_MARKER.finditer(text))
        if markers and (len(markers) != 1 or markers[0].start() != 0 or int(markers[0].group(1)) != number):
            reasons.append("source_page_marker_identity_unproved")
        if not text or not _PAGE_MARKER.sub("", text).strip():
            reasons.append("empty_selected_page_range_unproved")
        chunks.append(text)
    if numbers != sorted(numbers):
        reasons.append("source_page_order_unproved")
    return chunks, reasons


def _assets(source_pages, asset_paths, layout_result):
    from mathbank.paths import PROJECT_ROOT
    images = []
    for row in source_pages:
        images.extend(f.get("image_path") for f in row.get("figures", []) if isinstance(f, dict) and f.get("image_path"))
        images.extend(m.group(1) for m in re.finditer(r"!\[[^\]\n]*\]\(([^\n)]+)\)", str(row.get("markdown", row.get("text", "")) or "")))
    if isinstance(layout_result, dict):
        for row in layout_result.get("pages", []):
            images.extend(f.get("image_path") for f in row.get("figures", [])
                          if isinstance(f, dict) and f.get("image_path"))
    records = []
    for url in sorted(set(images)):
        if not isinstance(url, str) or not re.fullmatch(r"/static/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:png|jpe?g|webp)", url, re.I):
            raise ValueError("source_asset_path_invalid")
        path = Path((asset_paths or {}).get(url, PROJECT_ROOT / url.lstrip("/")))
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("source_asset_unavailable")
        records.append({"url": url, "path": str(path), "sha256": _sha(path.read_bytes())})
    return records


def _assets_unchanged(records):
    try:
        return all(Path(r["path"]).is_file() and not Path(r["path"]).is_symlink()
                   and _sha(Path(r["path"]).read_bytes()) == r["sha256"] for r in records)
    except (ValueError, KeyError, OSError):
        return False


def finalize_pdf_source_review_evidence(source, diagnostics, *, source_document_sha256,
        source_pages, layout_result, native_evidence, asset_paths=None):
    """Bind exact main-pipeline source/page/layout states; do not certify OCR."""
    native = _open(_NativePdfEvidence, native_evidence)
    reasons, pages = [], []
    if native is None or native["source_document_sha256"] != source_document_sha256:
        reasons.append("native_producer_proof_unavailable")
        native = {"pages": [], "global_reasons": [], "analysis": {}}
    reasons.extend(native["global_reasons"])
    if (not isinstance(source, str) or len(source) > MAX_SOURCE_CHARACTERS
            or not isinstance(source_pages, list) or len(source_pages) > MAX_PAGES
            or any(not isinstance(row, dict) or not isinstance(row.get("figures", []), list) for row in source_pages)):
        return None
    chunks, chunk_reasons = _page_chunks(source_pages)
    reasons.extend(chunk_reasons)
    marked = "\n\n".join(chunks)
    unmarked_chunks = [_PAGE_MARKER.sub("", chunk) for chunk in chunks]
    unmarked = "\n\n".join(unmarked_chunks)
    if source == unmarked:
        bound_chunks = unmarked_chunks
    elif source == marked:
        bound_chunks = chunks
    else:
        reasons.append("final_source_not_exact_page_merge")
        bound_chunks = unmarked_chunks
    native_by_number = {r["page_number"]: r for r in native["pages"]}
    layout_pages = {}
    if isinstance(layout_result, dict):
        for row in layout_result.get("pages", []):
            index = row.get("page_index")
            if type(index) is not int or index < 0 or index + 1 in layout_pages:
                reasons.append("layout_page_identity_unproved")
            else:
                layout_pages[index + 1] = row
    if list(native_by_number) != [r.get("page_number") for r in source_pages]:
        reasons.append("final_selected_page_coverage_unproved")
    offset = 0
    for index, (row, chunk, bound) in enumerate(zip(source_pages, chunks, bound_chunks)):
        number = row.get("page_number")
        assessment = native_by_number.get(number, {})
        origin = row.get("origin")
        visual = origin in {"ocr", "joint_vision", "regional_vision", "vision"}
        reliable = bool(assessment.get("reliable")) and origin in {"native", "native_repaired"}
        local_reasons = list(assessment.get("reasons", [])) if not visual else ["visual_origin_not_native_certified"]
        global_reasons = list(assessment.get("global_reasons", [])) if not visual else []
        if origin not in {"native", "native_repaired", "ocr", "joint_vision", "regional_vision", "vision"}:
            global_reasons.append("source_origin_unrecognized")
        expected_origin = "native_repaired" if assessment.get("native_origin") == "native-math-repaired" else "native"
        if not visual and origin != expected_origin:
            reliable = False
            global_reasons.append("native_origin_changed_after_producer")
        plain = _PAGE_MARKER.sub("", chunk).strip()
        if not visual and _sha(plain) != assessment.get("native_markdown_sha256"):
            reliable = False
            local_reasons.append("native_source_changed_after_producer")
            global_reasons.append("native_source_changed_after_producer")
        figures = row.get("figures", [])
        layout_page = layout_pages.get(number, {})
        if not visual and (figures or layout_page.get("figures") or layout_page.get("warnings")):
            reliable = False
            global_reasons.append("native_final_figure_ownership_unproved")
        end = offset + len(bound)
        pages.append({"page_index": number - 1 if type(number) is int else None, "page_number": number,
            "range": [offset, end], "origin": origin, "reliable": reliable and not local_reasons and not global_reasons,
            "reasons": sorted(set(local_reasons)), "global_reasons": sorted(set(global_reasons)),
            "glyph_summary": assessment.get("glyph_summary", {}), "reading_order": assessment.get("reading_order", {}),
            "figure_ownership": assessment.get("figure_ownership", {})})
        reasons.extend(global_reasons)
        offset = end + (2 if index + 1 < len(bound_chunks) else 0)
    try:
        assets = _assets(source_pages, asset_paths, layout_result)
        return _seal(_PdfSourceReviewEvidence, {"schema": "mathbank.pdf-source-review-evidence.v1",
            "source_sha256": _sha(source), "source_document_sha256": source_document_sha256,
            "diagnostics_sha256": _diagnostic_hash(diagnostics), "source_pages_sha256": _sha(_json(source_pages)),
            "layout_sha256": _sha(_json(layout_result)), "pages": pages, "assets": assets,
            "global_reasons": sorted(set(reasons)), "producer_analysis": native.get("analysis", {})})
    except (ValueError, TypeError, KeyError, OSError):
        return None


def verify_pdf_source_review_evidence(source, diagnostics, evidence, *, source_document_sha256,
        source_pages, layout_result):
    """Late verification is source/layout/hash/asset checks, no PDF reanalysis."""
    rejected = {"status": "uncertain", "pages": [], "global_reasons": ["pdf_source_review_proof_invalid"]}
    payload = _open(_PdfSourceReviewEvidence, evidence)
    if payload is None:
        return rejected
    try:
        if (payload["source_sha256"] != _sha(source) or payload["source_document_sha256"] != source_document_sha256
                or payload["diagnostics_sha256"] != _diagnostic_hash(diagnostics)
                or payload["source_pages_sha256"] != _sha(_json(source_pages)) or payload["layout_sha256"] != _sha(_json(layout_result))
                or not _assets_unchanged(payload["assets"])):
            return rejected
    except (TypeError, ValueError, KeyError):
        return rejected
    result = {k: v for k, v in payload.items() if k != "assets"}
    return {**result, "status": "uncertain" if payload["global_reasons"] else "ready",
            "proof_sha256": _sha(evidence.payload), "asset_count": len(payload["assets"]),
            "verification_analysis": {"pdf_opens": 0, "pages_reanalyzed": 0}}
