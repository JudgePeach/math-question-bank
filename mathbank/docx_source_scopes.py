"""Process-local, source-bound proof of native Word review origin ranges.

This proof locates extraction risks; it never erases their global diagnostics
or certifies question independence. Public JSON or caller-authored flags cannot
stand in for a journal captured by the native extractor.
"""
from dataclasses import dataclass
from collections import Counter
from collections.abc import Mapping
import hashlib
import hmac
import json
from pathlib import Path
import secrets


_SECRET = secrets.token_bytes(32)
_RISK_COUNTERS = (
    "review_required", "omml_unsupported", "mtef_fallback_images", "mtef_unavailable",
    "images_unavailable", "symbols_unavailable", "numbering_unavailable", "tables_review_required",
    "native_missing_glyphs",
)
_RISK_LISTS = ("warnings", "unsupported_omml_tags", "heading_image_ownership")
MAX_SCOPE_NORMALIZATION_CHARACTERS = 4_000_000
_NATIVE_DIAGNOSTIC_KEYS = (
    "omml_converted", "omml_unsupported", "omml_private_chars_converted", "omml_control_words_repaired",
    "mtef_converted", "mtef_annotation_converted", "mtef_structural_converted", "mtef_compatibility_converted",
    "mtef_private_chars_converted", "mtef_control_words_repaired", "mtef_fallback_images", "mtef_unavailable",
    "images_extracted", "images_unavailable", "symbols_converted", "symbols_unavailable",
    "numbering_converted", "numbering_unavailable", "superscripts_converted", "subscripts_converted",
    "underlines_converted", "text_styles_converted", "tables_review_required", "unsupported_omml_tags",
    "warnings", "review_required", "asset_paths", "heading_image_ownership",
    "native_missing_glyphs",
    "mtef_ignored_spacing_codes",
)


@dataclass(frozen=True)
class _WordReviewEvidence:
    payload: str
    signature: str


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _diagnostic_hash(diagnostics) -> str:
    if not isinstance(diagnostics, Mapping):
        raise ValueError("diagnostics_not_mapping")
    return _sha(_json({key: diagnostics.get(key, 0) if key == "native_missing_glyphs"
                      else diagnostics.get(key, []) if key in {"asset_paths", "heading_image_ownership", "mtef_ignored_spacing_codes"}
                      else diagnostics[key] for key in _NATIVE_DIAGNOSTIC_KEYS}))


def _assets_unchanged(assets) -> bool:
    try:
        for record in assets:
            path = Path(record["absolute_path"])
            if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != record["normalized_sha256"]):
                return False
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return True


def _review_snapshot(diagnostics) -> dict:
    state = {}
    for key in _RISK_COUNTERS:
        value = diagnostics.get(key, 0) if key == "native_missing_glyphs" else diagnostics.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("invalid_review_counter")
        state[key] = value
    for key in _RISK_LISTS:
        value = diagnostics.get(key, [])
        if not isinstance(value, list):
            raise ValueError("invalid_review_list")
        state[key] = json.loads(_json(value))
    return state


def _state_delta(before, after) -> tuple[dict, list[str]]:
    delta, problems = {}, []
    for key in _RISK_COUNTERS:
        amount = after[key] - before[key]
        if amount < 0:
            problems.append("nonmonotonic_review_counter")
        if amount:
            delta[key] = amount
    for key in _RISK_LISTS:
        earlier, later = before[key], after[key]
        if later[:len(earlier)] != earlier:
            problems.append("nonmonotonic_review_list")
        delta[key] = later[len(earlier):]
    return delta, problems


def _has_risk(delta) -> bool:
    return (any(delta.get(key, 0) for key in _RISK_COUNTERS)
            or bool(delta.get("warnings") or delta.get("unsupported_omml_tags"))
            or bool(delta.get("structural_risks"))
            or any(row.get("status") == "ownership_uncertain" for row in delta.get("heading_image_ownership", [])))


def _finalize_source_review_evidence(source, diagnostics, document_sha256, prelude, journal, normalize, assets):
    """Mint only from the extractor's per-body-child snapshots and raw blocks."""
    reasons = []
    if (list(diagnostics.get("asset_paths", [])) != [record["url"] for record in assets]
            or diagnostics["images_extracted"] != len(assets) or not _assets_unchanged(assets)):
        reasons.append("source_assets_not_completely_bound")
    if (not isinstance(document_sha256, str) or len(document_sha256) != 64
            or any(c not in "0123456789abcdef" for c in document_sha256)):
        reasons.append("original_document_digest_unavailable")
    if _has_risk(prelude):
        reasons.append("pre_body_review_origin_unlocated")
    final = _review_snapshot(diagnostics)
    previous = prelude
    prepared = []
    tally = Counter()
    tags = set()
    for item in journal:
        if item["before"] != previous:
            reasons.append("journal_snapshot_gap")
        delta, errors = _state_delta(item["before"], item["after"])
        delta["structural_risks"] = item.get("structural_risks", [])
        reasons.extend(errors)
        previous = item["after"]
        tally.update({key: delta.get(key, 0) for key in _RISK_COUNTERS})
        tags.update(delta["unsupported_omml_tags"])
        raw = "\n\n".join(item["blocks"]).strip()
        piece = normalize(raw)
        if not piece and _has_risk(delta):
            reasons.append("empty_output_review_origin_unlocated")
        if piece:
            prepared.append({"raw": raw, "piece": piece, "origins": [(item, delta)]})
    # The existing global normalizer may consume punctuation from the previous
    # Word paragraph before a new subquestion. Merge only a boundary whose
    # exact same-normalizer result proves the two pieces cannot stand alone.
    # Every origin's delta survives; the merged range is conservatively atomic.
    normalization_characters = sum(len(group["raw"]) for group in prepared)
    cursor = 0
    while cursor + 1 < len(prepared):
        left, right = prepared[cursor:cursor + 2]
        raw = left["raw"] + "\n\n" + right["raw"]
        normalization_characters += len(raw)
        if normalization_characters > MAX_SCOPE_NORMALIZATION_CHARACTERS:
            reasons.append("per_block_normalization_budget_exceeded")
            break
        joined = normalize(raw)
        if joined != left["piece"] + "\n\n" + right["piece"]:
            prepared[cursor:cursor + 2] = [{"raw": raw, "piece": joined,
                "origins": left["origins"] + right["origins"]}]
            cursor = max(0, cursor - 1)
        else:
            cursor += 1
    for key in _RISK_COUNTERS:
        if final[key] != prelude[key] + tally[key]:
            reasons.append("final_review_counter_not_accounted")
    if sorted(final["unsupported_omml_tags"]) != sorted(previous["unsupported_omml_tags"]):
        reasons.append("final_review_tags_not_accounted")
    if final["heading_image_ownership"] != previous["heading_image_ownership"]:
        reasons.append("final_image_ownership_not_accounted")
    expected_warnings = list(previous["warnings"])
    aggregate = "部分 Office 公式含暂未完整支持的结构：" + ", ".join(sorted(final["unsupported_omml_tags"]))
    if final["unsupported_omml_tags"] and aggregate not in expected_warnings:
        if tags != set(final["unsupported_omml_tags"]):
            reasons.append("aggregate_review_tags_origin_unlocated")
        expected_warnings.append(aggregate)
    if final["warnings"] != expected_warnings:
        reasons.append("final_review_warning_not_accounted")
    if "\n\n".join(group["piece"] for group in prepared) != source:
        reasons.append("per_block_normalization_not_equal_to_source")
    blocks, offset = [], 0
    for index, group in enumerate(prepared):
        piece = group["piece"]
        origins = group["origins"]
        item = origins[0][0]
        delta = {key: sum(d.get(key, 0) for _, d in origins) for key in _RISK_COUNTERS}
        delta.update({key: [value for _, d in origins for value in d.get(key, [])]
                      for key in (*_RISK_LISTS, "structural_risks")})
        end = offset + len(piece) + (2 if index + 1 < len(prepared) else 0)
        related_numbers = sorted({number for row in delta["heading_image_ownership"]
            if row.get("status") == "ownership_uncertain"
            for number in (row.get("source_number"), row.get("possible_adjacent_source_number"))
            if type(number) is int and number > 0})
        block = {"range": [offset, end], "body_child_index": item["body_child_index"],
                 "body_child_indices": [origin["body_child_index"] for origin, _ in origins],
                 "output_block_indices": [item["output_block_indices"][0], origins[-1][0]["output_block_indices"][1]],
                 "risk_counters": {key: delta[key] for key in _RISK_COUNTERS if delta.get(key)},
                 "warnings": delta["warnings"], "unsupported_omml_tags": delta["unsupported_omml_tags"],
                 "heading_image_ownership": delta["heading_image_ownership"],
                 "structural_risks": delta["structural_risks"],
                 "risk_related_numbers": related_numbers,
                 "has_risk": _has_risk(delta), "source_sha256": _sha(source[offset:end])}
        block["origins"] = [{"body_child_index": origin["body_child_index"],
                             "output_block_indices": origin["output_block_indices"],
                             "risk_counters": {key: d[key] for key in _RISK_COUNTERS if d.get(key)},
                             **{key: d.get(key, []) for key in (*_RISK_LISTS, "structural_risks")}}
                            for origin, d in origins]
        if delta["unsupported_omml_tags"] and aggregate in final["warnings"]:
            block["derived_aggregate_warning"] = aggregate
        blocks.append(block)
        offset = end
    if offset != len(source):
        reasons.append("normalized_block_coverage_incomplete")
    payload = _json({"schema": "mathbank.docx-source-review-evidence.v1",
        "source_sha256": _sha(source), "source_document_sha256": document_sha256,
        "diagnostics_sha256": _diagnostic_hash(diagnostics), "blocks": blocks,
        "assets": assets,
        "risk_related_numbers": sorted({number for block in blocks for number in block["risk_related_numbers"]}),
        "unlocated_reasons": sorted(set(reasons))})
    return _WordReviewEvidence(payload, hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest())


def verify_source_review_evidence(source, diagnostics, evidence, *, source_document_sha256=None) -> dict:
    """Return trusted scopes only when the private proof and source still match."""
    rejected = {"status": "uncertain", "blocks": [], "unlocated_reasons": ["source_review_proof_invalid"]}
    if (type(evidence) is not _WordReviewEvidence or not isinstance(source, str)
            or not isinstance(evidence.payload, str) or not isinstance(evidence.signature, str)):
        return rejected
    expected = hmac.new(_SECRET, evidence.payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(evidence.signature, expected):
        return rejected
    try:
        payload = json.loads(evidence.payload)
        if (payload["source_sha256"] != _sha(source)
                or payload["diagnostics_sha256"] != _diagnostic_hash(diagnostics)
                or not _assets_unchanged(payload["assets"])
                or source_document_sha256 is not None and payload["source_document_sha256"] != source_document_sha256):
            return rejected
    except (TypeError, ValueError, KeyError):
        return rejected
    payload["asset_count"] = len(payload.pop("assets"))
    if payload["unlocated_reasons"]:
        return {**payload, "status": "uncertain", "blocks": [], "proof_sha256": _sha(evidence.payload)}
    return {**payload, "status": "ready", "proof_sha256": _sha(evidence.payload)}
