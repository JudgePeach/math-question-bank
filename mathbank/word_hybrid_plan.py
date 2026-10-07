"""Source-bound Word groups for one metadata/full-split mixed request.

This planner never resets extraction diagnostics and never signs source quality.
Native groups must obtain the source_metadata module's private group certificate.
Unknown diagnostic origins or global boundary problems keep the original route.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import secrets

from mathbank import source_metadata
from mathbank.docx_source_scopes import verify_source_review_evidence


MAX_GROUPS = 32
MAX_SOURCE_CHARACTERS = 500_000
MAX_QUESTIONS = 1_000
MIN_METADATA_SOURCE_CHARACTERS = 256
_SECRET = secrets.token_bytes(32)
_LOCAL_REASONS = frozenset({
    "word_extraction_requires_review", "unresolved_source_characters",
    "answer_embedded_in_question", "incomplete_or_duplicate_option_labels",
    "incomplete_or_nonstandard_choices", "empty_option", "multiple_choices_environments",
    "ambiguous_original_correct_marker", "correct_marker_without_choices",
    "conflicting_original_answer_sources", "source_reconciliation_requires_review",
    "cross_question_dependencies",
})
_RISK_COUNTERS = (
    "review_required", "omml_unsupported", "mtef_fallback_images", "mtef_unavailable",
    "images_unavailable", "symbols_unavailable", "numbering_unavailable", "tables_review_required",
)
_GROUP_PUBLIC_FIELDS = (
    "id", "source_numbers", "question_ids", "owned_ranges", "context_ranges", "source_range",
    "route", "reasons", "scope_sha256",
)


class WordHybridPlanError(ValueError):
    """A fixed, content-free source-plan failure suitable for task diagnostics."""


@dataclass(frozen=True)
class _HybridPlanCertificate:
    signature: str


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _snapshot(plan: dict) -> str:
    return _json({
        "schema": plan.get("schema"), "mode": plan.get("mode"),
        "source_sha256": _sha(plan.get("source", "")),
        "document_sha256": plan.get("document_sha256"),
        "task_id": plan.get("task_id"), "generation": plan.get("generation"),
        "native_diagnostics": plan.get("_native_diagnostics"),
        "scope_proof_sha256": plan.get("scope_proof_sha256"),
        "global_reasons": plan.get("global_reasons"),
        "inspection": {key: plan.get("_inspection", {}).get(key) for key in
                       ("source_sha256", "questions", "source_ranges", "document_metadata", "source_dependencies", "fallback_reasons")},
        "groups": [{key: group.get(key) for key in _GROUP_PUBLIC_FIELDS} for group in plan.get("groups", [])],
        "request_plan": plan.get("request_plan"), "fallback_policy": plan.get("fallback_policy"),
        "reuse_policy": plan.get("reuse_policy"),
    })


def _seal(plan: dict) -> dict:
    plan["_hybrid_plan_certificate"] = _HybridPlanCertificate(
        hmac.new(_SECRET, _snapshot(plan).encode("utf-8"), hashlib.sha256).hexdigest()
    )
    return plan


def _ranges(values, source: str) -> list[list[int]]:
    result = []
    for value in values:
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or any(type(number) is not int for number in value)
                or not 0 <= value[0] < value[1] <= len(source)):
            raise WordHybridPlanError("invalid_source_ranges")
        pair = list(value)
        if pair not in result:
            result.append(pair)
    result.sort()
    if any(left[1] > right[0] for left, right in zip(result, result[1:])):
        raise WordHybridPlanError("overlapping_source_ranges")
    return result


def _intersects(left, right) -> bool:
    return max(left[0], right[0]) < min(left[1], right[1])


def _risk_state(diagnostics) -> tuple[bool, list[str]]:
    if not isinstance(diagnostics, Mapping):
        return True, ["extraction_diagnostics_missing"]
    risk = False
    for key in _RISK_COUNTERS:
        value = diagnostics.get(key)
        if type(value) is not int or value < 0:
            return True, ["extraction_diagnostics_incomplete"]
        risk |= value > 0
    for key in ("warnings", "unsupported_omml_tags"):
        value = diagnostics.get(key)
        if not isinstance(value, list):
            return True, ["extraction_diagnostics_incomplete"]
        risk |= bool(value)
    return risk, []


def _require_group(plan: dict, group: dict) -> None:
    validator = getattr(source_metadata, "require_word_source_group_certificate", None)
    if validator is None:
        raise WordHybridPlanError("group_certificate_interface_unavailable")
    validator(group["_source_metadata_plan"], group_id=group["id"],
              task_id=plan["task_id"], generation=plan["generation"])
    certificate = group["_source_metadata_plan"].get("_word_source_certificate")
    if (getattr(certificate, "original_source_sha256", None) != plan["source_sha256"]
            or getattr(certificate, "proof_sha256", None) != plan["scope_proof_sha256"]
            or getattr(certificate, "owned_ranges", None) != tuple(map(tuple, group["owned_ranges"]))
            or getattr(certificate, "context_ranges", None) != tuple(map(tuple, group["context_ranges"]))
            or getattr(certificate, "original_question_ids", None) != tuple(group["question_ids"])):
        raise WordHybridPlanError("group_original_scope_changed")
    supplied = group["_source_metadata_plan"].get("questions", [])
    if ([row.get("id") for row in supplied] != group["question_ids"]
            or [row.get("source_number") for row in supplied] != group["source_numbers"]):
        raise WordHybridPlanError("group_question_identity_changed")


def require_word_hybrid_plan(plan: dict, *, task_id: str, generation: int) -> None:
    """Validate before and after requests; model-known IDs cannot mint a plan."""
    certificate = plan.get("_hybrid_plan_certificate") if isinstance(plan, dict) else None
    if (type(certificate) is not _HybridPlanCertificate or plan.get("task_id") != task_id
            or type(generation) is not int or plan.get("generation") != generation):
        raise WordHybridPlanError("hybrid_plan_identity_invalid")
    try:
        signature = hmac.new(_SECRET, _snapshot(plan).encode("utf-8"), hashlib.sha256).hexdigest()
    except (TypeError, ValueError, KeyError):
        raise WordHybridPlanError("hybrid_plan_snapshot_invalid") from None
    if not hmac.compare_digest(certificate.signature, signature):
        raise WordHybridPlanError("hybrid_plan_snapshot_changed")
    if plan.get("source_sha256") != _sha(plan.get("source", "")):
        raise WordHybridPlanError("hybrid_source_changed")
    evidence = plan.get("_source_review_evidence")
    if evidence is not None and plan["mode"] != "original":
        proof = verify_source_review_evidence(plan["source"], plan["_native_diagnostics"], evidence,
                                              source_document_sha256=plan.get("document_sha256"))
        if proof.get("status") != "ready" or proof.get("proof_sha256") != plan.get("scope_proof_sha256"):
            raise WordHybridPlanError("hybrid_source_review_proof_changed")
    if plan["mode"] == "whole_metadata":
        source_metadata._require_certificate(plan["_whole_source_metadata_plan"])
        if plan["_whole_source_metadata_plan"].get("source") != plan["source"]:
            raise WordHybridPlanError("whole_source_plan_changed")
    for group in plan["groups"]:
        if group["route"] == "metadata" and plan["mode"] == "hybrid":
            _require_group(plan, group)
        elif group["route"] == "split" and "_source_metadata_plan" in group:
            raise WordHybridPlanError("risky_group_contains_native_certificate")


def build_word_hybrid_plan(
    source: str, diagnostics: Mapping, *, task_id: str, generation: int,
    source_document_sha256: str | None = None, source_review_evidence=None,
    asset_evidence=None, min_metadata_source_characters: int = MIN_METADATA_SOURCE_CHARACTERS,
    min_metadata_source_fraction: float = 0.0,
) -> dict:
    """Plan exact owned source spans and conservative cross-question closure.

    Only the native extractor's private complete-scope evidence locates positive
    global review counters. Public marker text may reject a group but cannot
    authorize clearing global diagnostics. No page number or physical nearest-
    question heuristic is used for ownership.
    """
    if (not isinstance(task_id, str) or not task_id or len(task_id) > 200
            or type(generation) is not int or generation < 0
            or type(min_metadata_source_characters) is not int or min_metadata_source_characters < 0
            or type(min_metadata_source_fraction) not in (int, float)
            or not math.isfinite(min_metadata_source_fraction) or not 0 <= min_metadata_source_fraction <= 1):
        raise WordHybridPlanError("hybrid_task_identity_invalid")
    source = source if isinstance(source, str) else ""
    plan = {
        "schema": "mathbank.word-hybrid-plan.v1", "mode": "original", "source": source,
        "source_sha256": _sha(source), "document_sha256": source_document_sha256,
        "task_id": task_id, "generation": generation, "groups": [], "global_reasons": [],
        "scope_proof_sha256": None, "request_plan": [],
        "reuse_policy": {"minimum_metadata_source_fraction": min_metadata_source_fraction,
                         "metadata_source_fraction": 0.0, "is_token_measurement": False},
        "fallback_policy": {"max_additional_posts": 1, "whole_only_when_no_valid_results": True,
                            "otherwise_failed_groups_only": True, "repeat_completed_groups": False},
        "_native_diagnostics": deepcopy(dict(diagnostics)) if isinstance(diagnostics, Mapping) else {},
        "_source_review_evidence": source_review_evidence,
    }

    def original(reason):
        plan["mode"] = "original"
        plan["global_reasons"] = sorted(set([*plan["global_reasons"], reason]))
        plan["request_plan"] = []
        return _seal(plan)

    if not source.strip() or len(source) > MAX_SOURCE_CHARACTERS:
        return original("source_missing_or_excessive")
    global_risk, diagnostic_errors = _risk_state(diagnostics)
    if diagnostic_errors:
        plan["global_reasons"].extend(diagnostic_errors)
        return original("diagnostic_origin_unavailable")
    proof = None
    if source_review_evidence is not None:
        if not isinstance(source_document_sha256, str) or len(source_document_sha256) != 64:
            return original("document_digest_missing")
        proof = verify_source_review_evidence(source, diagnostics, source_review_evidence,
                                              source_document_sha256=source_document_sha256)
        if proof.get("status") != "ready":
            return original("diagnostic_origin_unavailable")
        plan["scope_proof_sha256"] = proof["proof_sha256"]
    elif global_risk:
        return original("positive_global_diagnostics_unlocated")
    try:
        inspection = source_metadata.prepare_word_source_metadata(
            source, diagnostics, asset_evidence=asset_evidence, inspect_ineligible=True,
        )
    except Exception:
        return original("source_inspection_unavailable")
    plan["_inspection"] = inspection
    reasons = inspection.get("fallback_reasons", [])
    fatal = [reason for reason in reasons if reason not in _LOCAL_REASONS]
    if fatal:
        plan["global_reasons"].extend(fatal)
        return original("global_source_boundary_uncertain")
    questions = inspection.get("questions", [])
    if not questions or len(questions) > MAX_QUESTIONS:
        return original("question_roster_unavailable")
    try:
        covered = _ranges([[row["start"], row["end"]] for row in inspection["source_ranges"]], source)
        if (covered[0][0] != 0 or covered[-1][1] != len(source)
                or any(left[1] != right[0] for left, right in zip(covered, covered[1:]))):
            return original("source_partition_incomplete")
        owned_by_question = []
        identifiers = []
        numbers = defaultdict(list)
        for index, question in enumerate(questions):
            identifiers.append(question["id"])
            numbers[question["source_number"]].append(index)
            owned = [question["source_range"]]
            if question.get("answer_source_range") is not None:
                owned.append(question["answer_source_range"])
            owned_by_question.append(_ranges(owned, source))
            if source[slice(*question["source_range"])] != question["raw_content"]:
                return original("question_source_changed")
            if question.get("answer_source_range") is not None and source[slice(*question["answer_source_range"])] != question["raw_answer"]:
                return original("answer_source_changed")
        if len(set(identifiers)) != len(identifiers) or any(type(number) is not int or len(members) != 1 for number, members in numbers.items()):
            return original("question_identity_ambiguous")
        _ranges([bounds for ranges in owned_by_question for bounds in ranges], source)
        context_records = inspection["document_metadata"]
        contexts = _ranges([[row["start"], row["end"]] for row in context_records], source)
        if any(source[row["start"]:row["end"]] != row["text"] for row in context_records):
            return original("context_source_changed")
    except (TypeError, ValueError, KeyError, IndexError):
        return original("source_ranges_invalid")
    by_identifier = {value: index for index, value in enumerate(identifiers)}
    parents = list(range(len(questions)))
    risks = defaultdict(set)

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(members, reason):
        members = sorted(set(members))
        if not members:
            return False
        for index in members:
            parents[find(index)] = find(members[0])
            risks[index].add(reason)
        return True

    dependency = inspection.get("source_dependencies", {})
    if (dependency.get("status") != "complete" or dependency.get("has_unresolved")):
        return original("dependency_scope_not_exhaustive")
    for reference in dependency.get("references", []):
        if reference.get("unresolved") or not reference.get("scope_exhaustive"):
            return original("dependency_scope_not_exhaustive")
        members = [by_identifier.get(value) for value in reference.get("member_ids", [])]
        if not members or any(index is None for index in members):
            return original("dependency_identity_ambiguous")
        join(members, "cross_question_dependency")
    for index, question in enumerate(questions):
        _, _, local_reasons, _ = source_metadata._canonical_body(question["raw_content"])
        if local_reasons:
            risks[index].update(local_reasons)
        answer_range = question.get("answer_source_range")
        if answer_range is not None:
            start = answer_range[0]
            if source[start:start + 1] not in {"\n", "\r"} and source[:start].rsplit("\n", 1)[-1].strip():
                risks[index].add("answer_embedded_in_question")
    if proof is not None:
        for block in proof["blocks"]:
            if not (block.get("has_risk") or block.get("structural_risks")):
                continue
            bounds = block["range"]
            if block.get("structural_risks"):
                return original("native_structural_ownership_unknown")
            for record in block.get("heading_image_ownership", []):
                if record.get("status") != "ownership_uncertain":
                    continue
                reason = record.get("reason")
                if reason not in {"following_question_refers_unplaced_figure", "rendered_heading_not_safely_separable"}:
                    return original("native_image_ownership_unknown")
                if reason == "following_question_refers_unplaced_figure":
                    related = {record.get("source_number"), record.get("possible_adjacent_source_number")}
                    if len(related) != 2 or any(type(number) is not int or number not in block.get("risk_related_numbers", []) for number in related):
                        return original("native_image_ownership_unknown")
            members = [index for index, ranges in enumerate(owned_by_question)
                       if any(_intersects(bounds, owned) for owned in ranges)]
            for number in block.get("risk_related_numbers", []):
                if type(number) is not int or number not in numbers or len(numbers[number]) != 1:
                    return original("risk_related_question_unknown")
                members.extend(numbers[number])
            if any(_intersects(bounds, context) for context in contexts) or not join(members, "native_extraction_risk"):
                return original("native_risk_outside_owned_questions")
    # Marker text is rejection evidence, never a clean extraction certificate.
    for match in source_metadata._SOURCE_RISK.finditer(source):
        members = [index for index, ranges in enumerate(owned_by_question)
                   if any(owned[0] <= match.start() < match.end() <= owned[1] for owned in ranges)]
        if proof is None or len(members) != 1 or not risks[members[0]]:
            return original("unverified_source_marker")
        risks[members[0]].add("unresolved_source_marker")
    grouped = defaultdict(list)
    for index in range(len(questions)):
        grouped[find(index)].append(index)
    if inspection.get("eligible") and not risks:
        source_metadata._require_certificate(inspection)
        plan["mode"] = "whole_metadata"
        plan["reuse_policy"]["metadata_source_fraction"] = 1.0
        plan["_whole_source_metadata_plan"] = inspection
        plan["request_plan"] = [{"kind": "existing_source_metadata", "max_posts": 1}]
        return _seal(plan)
    bundles = []
    for members in sorted(grouped.values(), key=lambda values: values[0]):
        clean = not any(risks[index] for index in members)
        if (bundles and clean and not any(risks[index] for index in bundles[-1])
                and bundles[-1][-1] + 1 == members[0]):
            bundles[-1].extend(members)
        else:
            bundles.append(list(members))
    if len(bundles) > MAX_GROUPS:
        return original("hybrid_group_limit")
    prepare_group = getattr(source_metadata, "prepare_word_source_group", None)
    if prepare_group is None:
        return original("group_certificate_interface_unavailable")
    for members in bundles:
        owned = _ranges([bounds for index in members for bounds in owned_by_question[index]], source)
        qids = [identifiers[index] for index in members]
        group_reasons = sorted(set().union(*(risks[index] for index in members)))
        group_id = "WHG_" + _sha(_json([plan["source_sha256"], task_id, generation, qids, owned]))[:24]
        group = {"id": group_id, "source_numbers": [questions[index]["source_number"] for index in members],
                 "question_ids": qids, "owned_ranges": owned, "context_ranges": deepcopy(contexts),
                 "source_range": [min(bounds[0] for bounds in owned), max(bounds[1] for bounds in owned)],
                 "route": "split", "reasons": group_reasons,
                 "scope_sha256": _sha(_json([qids, owned, contexts]))}
        if not group_reasons:
            try:
                local_plan = prepare_group(source, diagnostics, owned_ranges=owned, context_ranges=contexts,
                    group_id=group_id, task_id=task_id, generation=generation,
                    source_review_evidence=source_review_evidence, asset_evidence=asset_evidence)
                group["_source_metadata_plan"] = local_plan
                group["route"] = "metadata"
                _require_group(plan, group)
            except Exception:
                group.pop("_source_metadata_plan", None)
                group["route"] = "split"
                group["reasons"] = ["source_group_not_certified"]
        plan["groups"].append(group)
    plan["groups"].sort(key=lambda group: group["source_range"][0])
    native = [group for group in plan["groups"] if group["route"] == "metadata"]
    if not native:
        return original("no_certified_native_groups")
    reused = sum(right - left for group in native for left, right in group["owned_ranges"])
    if reused < min_metadata_source_characters:
        return original("native_reuse_below_character_policy")
    total_owned = sum(right - left for group in plan["groups"] for left, right in group["owned_ranges"])
    plan["reuse_policy"]["metadata_source_fraction"] = reused / total_owned
    if reused / total_owned < min_metadata_source_fraction:
        plan["groups"] = []
        return original("metadata_reuse_below_mixed_character_fraction_policy")
    plan["mode"] = "hybrid"
    plan["request_plan"] = [{
        "kind": "word_hybrid", "max_posts": 1,
        "metadata_group_ids": [group["id"] for group in native],
        "split_group_ids": [group["id"] for group in plan["groups"] if group["route"] == "split"],
        "source_characters": len(source), "reused_native_characters": reused,
        "character_policy_is_token_measurement": False,
    }]
    return _seal(plan)


def word_hybrid_plan_diagnostics(plan: dict) -> dict:
    """Content-free summary; private plans/certificates are never API evidence."""
    return {"schema": plan.get("schema"), "mode": plan.get("mode"),
            "source_sha256": plan.get("source_sha256"), "document_sha256": plan.get("document_sha256"),
            "group_count": len(plan.get("groups", [])),
            "metadata_groups": sum(group.get("route") == "metadata" for group in plan.get("groups", [])),
            "split_groups": sum(group.get("route") == "split" for group in plan.get("groups", [])),
            "global_reasons": list(plan.get("global_reasons", [])),
            "reuse_policy": deepcopy(plan.get("reuse_policy", {})),
            "scope_proof_sha256": plan.get("scope_proof_sha256"),
            "primary_posts": 1 if plan.get("request_plan") else 0,
            "max_additional_posts": plan.get("fallback_policy", {}).get("max_additional_posts", 0)}
