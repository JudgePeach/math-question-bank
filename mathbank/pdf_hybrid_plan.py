"""Plan complete PDF questions without treating OCR text as native evidence.

The native PDF scope verifier and PDF metadata issuer own reliability. This
module only binds complete source ranges and closes explicit dependencies for
one bounded mixed request. Unknown page order or figure ownership keeps the
original whole-source route. Word extraction diagnostics/certificates are not
accepted by this path.
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
import re
import secrets

from mathbank import source_metadata
from mathbank.question_assets import embedded_question_assets, _mask_literals


MAX_GROUPS = 32
MAX_SOURCE_CHARACTERS = 500_000
MAX_QUESTIONS = 1_000
MAX_PAGES = 80
MAX_FIGURES = 2_048
MIN_METADATA_SOURCE_CHARACTERS = 256
_SECRET = secrets.token_bytes(32)
_NATIVE_ORIGINS = frozenset({"native", "native_repaired"})
_LOCAL_REASONS = frozenset({
    "unresolved_source_characters", "answer_embedded_in_question",
    "incomplete_or_duplicate_option_labels", "incomplete_or_nonstandard_choices",
    "empty_option", "multiple_choices_environments", "ambiguous_original_correct_marker",
    "correct_marker_without_choices", "conflicting_original_answer_sources",
    "source_reconciliation_requires_review", "cross_question_dependencies",
})
_GROUP_FIELDS = (
    "id", "source_numbers", "question_ids", "owned_ranges", "context_ranges",
    "source_range", "page_numbers", "figure_ids", "route", "reasons", "scope_sha256",
    "source_basis", "native_reliable",
)


class PdfHybridPlanError(ValueError):
    """Fixed local rejection messages; no source or provider secrets."""


@dataclass(frozen=True)
class _PdfHybridPlanCertificate:
    signature: str


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _snapshot(plan: dict) -> str:
    return _json({
        "schema": plan.get("schema"), "mode": plan.get("mode"),
        "source_basis": plan.get("source_basis"), "native_reliable": plan.get("native_reliable"),
        "source_sha256": _sha(plan.get("source", "")),
        "document_sha256": plan.get("document_sha256"), "task_id": plan.get("task_id"),
        "generation": plan.get("generation"), "diagnostics": plan.get("_source_diagnostics"),
        "source_pages": plan.get("_source_pages"), "layout_result": plan.get("_layout_result"),
        "scope_proof_sha256": plan.get("scope_proof_sha256"),
        "global_reasons": plan.get("global_reasons"), "inspection": plan.get("_inspection"),
        "groups": [{field: group.get(field) for field in _GROUP_FIELDS} for group in plan.get("groups", [])],
        "request_plan": plan.get("request_plan"), "fallback_policy": plan.get("fallback_policy"),
        "reuse_policy": plan.get("reuse_policy"),
    })


def _seal(plan: dict) -> dict:
    plan["_pdf_hybrid_plan_certificate"] = _PdfHybridPlanCertificate(
        hmac.new(_SECRET, _snapshot(plan).encode("utf-8"), hashlib.sha256).hexdigest())
    return plan


def _ranges(values, source: str) -> list[list[int]]:
    if not isinstance(values, (list, tuple)) or len(values) > MAX_QUESTIONS * 3:
        raise PdfHybridPlanError("invalid_source_ranges")
    result = []
    for value in values:
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or any(type(number) is not int for number in value)
                or not 0 <= value[0] < value[1] <= len(source)):
            raise PdfHybridPlanError("invalid_source_ranges")
        pair = list(value)
        if pair in result:
            raise PdfHybridPlanError("duplicate_source_ranges")
        result.append(pair)
    result.sort()
    if any(left[1] > right[0] for left, right in zip(result, result[1:])):
        raise PdfHybridPlanError("overlapping_source_ranges")
    return result


def _visible_intersection(source: str, left, right) -> bool:
    start, end = max(left[0], right[0]), min(left[1], right[1])
    return start < end and bool(source[start:end].strip())


def _verify(plan: dict) -> dict:
    if plan["source_basis"] == "native_pdf":
        from mathbank.pdf_source_scopes import verify_pdf_source_review_evidence as verifier
    elif plan["source_basis"] == "first_pass_transcription":
        from mathbank.pdf_transcription_scopes import verify_pdf_transcription_snapshot as verifier
    else:
        raise PdfHybridPlanError("pdf_source_basis_invalid")
    return verifier(plan["source"], plan["_source_diagnostics"],
        plan.get("_source_review_evidence"), source_document_sha256=plan["document_sha256"],
        source_pages=plan["_source_pages"], layout_result=plan["_layout_result"])


def _require_group(plan: dict, group: dict) -> None:
    if plan["source_basis"] == "native_pdf":
        from mathbank.pdf_source_metadata import require_pdf_source_group_certificate as validator
    else:
        from mathbank.pdf_source_metadata import require_pdf_transcription_group_certificate as validator
    local = group.get("_source_metadata_plan")
    validator(local, group_id=group["id"],
        task_id=plan["task_id"], generation=plan["generation"])
    certificate = local.get("_pdf_source_certificate")
    if (getattr(certificate, "original_source_sha256", None) != plan["source_sha256"]
            or getattr(certificate, "proof_sha256", None) != plan["scope_proof_sha256"]
            or getattr(certificate, "owned_ranges", None) != tuple(map(tuple, group["owned_ranges"]))
            or getattr(certificate, "context_ranges", None) != tuple(map(tuple, group["context_ranges"]))
            or getattr(certificate, "original_question_ids", None) != tuple(group["question_ids"])
            or getattr(certificate, "source_basis", None) != plan["source_basis"]
            or getattr(certificate, "native_reliable", None) is not (plan["source_basis"] == "native_pdf")):
        raise PdfHybridPlanError("group_original_scope_changed")
    supplied = local.get("questions", [])
    if ([row.get("id") for row in supplied] != group["question_ids"]
            or [row.get("source_number") for row in supplied] != group["source_numbers"]):
        raise PdfHybridPlanError("group_question_identity_changed")


def _require_pdf_plan(plan: dict, *, task_id: str, generation: int, source_basis: str) -> None:
    """Recheck source, native proof, assets and private group certs around HTTP."""
    certificate = plan.get("_pdf_hybrid_plan_certificate") if isinstance(plan, dict) else None
    if (type(certificate) is not _PdfHybridPlanCertificate or plan.get("task_id") != task_id
            or type(generation) is not int or plan.get("generation") != generation
            or plan.get("source_basis") != source_basis):
        raise PdfHybridPlanError("pdf_hybrid_identity_invalid")
    try:
        signature = hmac.new(_SECRET, _snapshot(plan).encode("utf-8"), hashlib.sha256).hexdigest()
    except (TypeError, ValueError, KeyError):
        raise PdfHybridPlanError("pdf_hybrid_snapshot_invalid") from None
    if not hmac.compare_digest(certificate.signature, signature):
        raise PdfHybridPlanError("pdf_hybrid_snapshot_changed")
    if plan.get("source_sha256") != _sha(plan.get("source", "")):
        raise PdfHybridPlanError("pdf_hybrid_source_changed")
    if plan.get("mode") != "original":
        verified = _verify(plan)
        if verified.get("status") != "ready" or verified.get("proof_sha256") != plan.get("scope_proof_sha256"):
            raise PdfHybridPlanError("pdf_hybrid_native_proof_changed")
    for group in plan.get("groups", []):
        if group.get("route") == "metadata":
            _require_group(plan, group)
        elif group.get("route") != "split" or "_source_metadata_plan" in group:
            raise PdfHybridPlanError("pdf_risky_group_contains_native_certificate")


def require_pdf_hybrid_plan(plan: dict, *, task_id: str, generation: int) -> None:
    """Require native PDF evidence; a first-pass transcript cannot substitute."""
    _require_pdf_plan(plan, task_id=task_id, generation=generation, source_basis="native_pdf")


def require_pdf_snapshot_hybrid_plan(plan: dict, *, task_id: str, generation: int) -> None:
    """Require machine-draft conservation, without certifying PDF accuracy."""
    _require_pdf_plan(plan, task_id=task_id, generation=generation, source_basis="first_pass_transcription")


def _page_accepted(page: dict, source_basis: str) -> bool:
    if source_basis == "native_pdf":
        return page.get("origin") in _NATIVE_ORIGINS and page.get("reliable") is True
    return page.get("snapshot_eligible") is True


def _context_accepted(page: dict, source_basis: str) -> bool:
    if source_basis == "native_pdf":
        return _page_accepted(page, source_basis)
    if page.get("source_basis") == "unverified_native_snapshot":
        return page.get("snapshot_eligible") is True
    return (page.get("origin") in {"joint_vision", "regional_vision"}
            and "no_verified_visual_transcript_witness" not in page.get("reasons", []))


def _figure_owners(source: str, layout: dict, page_indices: set[int], questions: list[dict]):
    """Own figures by actual safe image references, never nearest text/number."""
    assets_by_question = [set(embedded_question_assets(q["raw_content"], q["raw_answer"])) for q in questions]
    if layout is None and not any(assets_by_question):
        # The scope issuer still proves original bitmap/primitive coverage.
        # This supports a native path without an optional figure-layout stage.
        return []
    pages = layout.get("pages") if isinstance(layout, dict) else None
    if not isinstance(pages, list) or len(pages) != len(page_indices):
        raise PdfHybridPlanError("pdf_layout_page_coverage_unknown")
    page_seen, identifiers, slots, urls, records = set(), set(), set(), {}, []
    for page in pages:
        index = page.get("page_index") if isinstance(page, dict) else None
        if type(index) is not int or index not in page_indices or index in page_seen:
            raise PdfHybridPlanError("pdf_layout_page_identity_unknown")
        page_seen.add(index)
        if page.get("status") not in {"checked", "skipped"}:
            raise PdfHybridPlanError("pdf_figure_ownership_unknown")
        figures = page.get("figures")
        if not isinstance(figures, list):
            raise PdfHybridPlanError("pdf_figure_records_unknown")
        for figure in figures:
            if not isinstance(figure, dict):
                raise PdfHybridPlanError("pdf_figure_records_unknown")
            identifier, url = figure.get("id"), figure.get("image_path")
            bbox = figure.get("bbox")
            if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                    or not isinstance(url, str) or not url or url in urls
                    or not isinstance(figure.get("slot_id"), str) or not figure["slot_id"]
                    or (index, figure.get("slot_id")) in slots
                    or figure.get("attached") is not True
                    or type(figure.get("page_index", index)) is not int
                    or figure.get("page_index", index) != index
                    or not isinstance(bbox, (list, tuple)) or len(bbox) != 4
                    or any(isinstance(v, bool) or not isinstance(v, (int, float))
                           or not math.isfinite(v) for v in bbox)
                    or not (0 <= bbox[0] < bbox[2] <= 1000 and 0 <= bbox[1] < bbox[3] <= 1000)):
                raise PdfHybridPlanError("pdf_figure_identity_or_geometry_unknown")
            owners = [i for i, assets in enumerate(assets_by_question) if url in assets]
            if not owners:
                raise PdfHybridPlanError("pdf_figure_ownership_unknown")
            identifiers.add(identifier); slots.add((index, figure["slot_id"])); urls[url] = identifier
            records.append({"id": identifier, "page_index": index, "owners": owners,
                            "risk": bool(figure.get("review_reasons") or figure.get("review_required"))})
            if len(records) > MAX_FIGURES:
                raise PdfHybridPlanError("pdf_figure_limit")
    if any(assets - set(urls) for assets in assets_by_question):
        raise PdfHybridPlanError("pdf_unregistered_source_image")
    return records


def _build_pdf_plan(
    source: str, diagnostics: Mapping, *, source_pages, layout_result, task_id: str,
    generation: int, source_document_sha256: str, source_review_evidence=None,
    min_metadata_source_characters: int = MIN_METADATA_SOURCE_CHARACTERS,
    min_metadata_source_fraction: float = 0.0,
    source_basis: str,
) -> dict:
    """Plan exact stem/answer ranges, retaining cross-page and shared closures."""
    if (not isinstance(task_id, str) or not task_id or len(task_id) > 128
            or type(generation) is not int or generation < 0
            or type(min_metadata_source_characters) is not int or min_metadata_source_characters < 0
            or type(min_metadata_source_fraction) not in (int, float)
            or not math.isfinite(min_metadata_source_fraction) or not 0 <= min_metadata_source_fraction <= 1):
        raise PdfHybridPlanError("pdf_hybrid_task_identity_invalid")
    source = source if isinstance(source, str) else ""
    plan = {"schema": "mathbank.pdf-hybrid-plan.v1", "mode": "original", "source": source,
        "source_basis": source_basis, "native_reliable": False,
        "source_sha256": _sha(source), "document_sha256": source_document_sha256,
        "task_id": task_id, "generation": generation, "groups": [], "global_reasons": [],
        "scope_proof_sha256": None, "request_plan": [],
        "reuse_policy": {"minimum_metadata_source_fraction": min_metadata_source_fraction,
                         "metadata_source_fraction": 0.0, "is_token_measurement": False},
        "fallback_policy": {"max_additional_posts": 1, "whole_only_when_no_valid_results": True,
                            "otherwise_failed_groups_only": True, "repeat_completed_groups": False},
        "_source_diagnostics": deepcopy(dict(diagnostics)) if isinstance(diagnostics, Mapping) else {},
        "_source_pages": deepcopy(source_pages), "_layout_result": deepcopy(layout_result),
        "_source_review_evidence": source_review_evidence}

    def original(reason):
        plan["mode"] = "original"
        plan["global_reasons"] = sorted(set([*plan["global_reasons"], reason]))
        plan["request_plan"] = []
        plan["groups"] = []
        try:
            _snapshot(plan)
        except (TypeError, ValueError):
            # Invalid optional evidence cannot be signed or used locally. The
            # original source still goes to the established parser unchanged.
            plan["_source_diagnostics"] = {}
            plan["_source_pages"] = []
            plan["_layout_result"] = None
            plan.pop("_inspection", None)
            plan["global_reasons"].append("pdf_evidence_snapshot_invalid")
        return _seal(plan)

    if not source.strip() or len(source) > MAX_SOURCE_CHARACTERS:
        return original("source_missing_or_excessive")
    if (not isinstance(source_document_sha256, str) or len(source_document_sha256) != 64
            or any(char not in "0123456789abcdef" for char in source_document_sha256)):
        return original("pdf_document_digest_missing")
    if not isinstance(diagnostics, Mapping) or not isinstance(source_pages, list) or not 0 < len(source_pages) <= MAX_PAGES:
        return original("pdf_native_source_evidence_missing")
    try:
        proof = _verify(plan)
    except (ImportError, ValueError, TypeError, KeyError, OSError):
        return original("pdf_native_proof_unavailable")
    if proof.get("status") != "ready":
        return original("pdf_native_proof_unavailable")
    plan["scope_proof_sha256"] = proof.get("proof_sha256")
    if not isinstance(plan["scope_proof_sha256"], str) or len(plan["scope_proof_sha256"]) != 64:
        return original("pdf_native_proof_unavailable")
    if proof.get("global_reasons"):
        plan["global_reasons"].extend(proof["global_reasons"])
        return original("pdf_page_order_or_ownership_unknown")
    try:
        inspection = source_metadata.inspect_source_structure(source, inspect_ineligible=True)
    except Exception:
        return original("source_inspection_unavailable")
    plan["_inspection"] = inspection
    fatal = [reason for reason in inspection.get("fallback_reasons", []) if reason not in _LOCAL_REASONS]
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
        contexts = _ranges([[row["start"], row["end"]] for row in inspection["document_metadata"]], source)
        if any(source[row["start"]:row["end"]] != row["text"] for row in inspection["document_metadata"]):
            return original("context_source_changed")
        owned_by_question, identifiers, numbers = [], [], defaultdict(list)
        for i, question in enumerate(questions):
            identifiers.append(question["id"]); numbers[question["source_number"]].append(i)
            owned = [question["source_range"]]
            if question.get("answer_source_range") is not None:
                owned.append(question["answer_source_range"])
            owned_by_question.append(_ranges(owned, source))
            if source[slice(*question["source_range"])] != question["raw_content"]:
                return original("question_source_changed")
            if question.get("answer_source_range") is not None and source[slice(*question["answer_source_range"])] != question["raw_answer"]:
                return original("answer_source_changed")
        if (len(set(identifiers)) != len(identifiers)
                or any(type(number) is not int or len(members) != 1 for number, members in numbers.items())):
            return original("question_identity_ambiguous")
        _ranges([bounds for ranges in owned_by_question for bounds in ranges] + contexts, source)
        pages = proof["pages"]
        if not isinstance(pages, list) or len(pages) != len(source_pages):
            return original("pdf_page_coverage_unknown")
        page_indices, page_ranges, question_pages = set(), [], defaultdict(list)
        for page in pages:
            index, page_number = page["page_index"], page["page_number"]
            if (type(index) is not int or index < 0 or index in page_indices
                    or type(page_number) is not int or page_number != index + 1):
                return original("pdf_page_identity_unknown")
            page_indices.add(index)
            bounds = _ranges([page["range"]], source)[0]
            page_ranges.append(bounds)
            for i, owned in enumerate(owned_by_question):
                if any(_visible_intersection(source, bounds, value) for value in owned):
                    question_pages[i].append(page)
        _ranges(page_ranges, source)
        if (source[:page_ranges[0][0]].strip() or source[page_ranges[-1][1]:].strip()
                or any(left[1] > right[0] or source[left[1]:right[0]].strip()
                       for left, right in zip(page_ranges, page_ranges[1:]))):
            return original("pdf_page_source_coverage_unknown")
        if any(not question_pages[i] for i in range(len(questions))):
            return original("pdf_question_page_origin_unknown")
        selected_positions = {number: i for i, number in enumerate(sorted(index + 1 for index in page_indices))}
        for owned_pages in question_pages.values():
            numbers_for_question = sorted({page["page_number"] for page in owned_pages})
            if any(right - left != selected_positions[right] - selected_positions[left]
                   for left, right in zip(numbers_for_question, numbers_for_question[1:])):
                # Remote answer sections are allowed when every intervening
                # page was selected. Missing selected pages cannot prove a
                # complete cross-page question or its dependencies.
                return original("pdf_question_crosses_unselected_page_gap")
        if any(not _context_accepted(page, source_basis)
               and any(_visible_intersection(source, page["range"], context) for context in contexts)
               for page in pages):
            return original("pdf_document_context_not_native" if source_basis == "native_pdf"
                            else "pdf_document_context_snapshot_unproved")
        figures = _figure_owners(source, layout_result, page_indices, questions)
    except PdfHybridPlanError as exc:
        return original(str(exc))
    except (TypeError, ValueError, KeyError, IndexError):
        return original("source_ranges_or_layout_invalid")
    dependency = inspection.get("source_dependencies", {})
    if dependency.get("status") != "complete" or dependency.get("has_unresolved"):
        return original("dependency_scope_not_exhaustive")
    parents, risks = list(range(len(questions))), defaultdict(set)
    by_identifier = {identifier: i for i, identifier in enumerate(identifiers)}

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]; i = parents[i]
        return i

    def join(members, reason):
        members = sorted(set(members))
        for i in members:
            parents[find(i)] = find(members[0]); risks[i].add(reason)

    for reference in dependency.get("references", []):
        members = [by_identifier.get(identifier) for identifier in reference.get("member_ids", [])]
        if (reference.get("unresolved") or not reference.get("scope_exhaustive")
                or not members or any(i is None for i in members)):
            return original("dependency_scope_not_exhaustive")
        join(members, "cross_question_dependency")
    for i, question in enumerate(questions):
        _, _, local_reasons, _ = source_metadata._canonical_body(question["raw_content"])
        risks[i].update(local_reasons)
        if any(not _page_accepted(page, source_basis) for page in question_pages[i]):
            risks[i].add("pdf_non_native_or_risky_page" if source_basis == "native_pdf"
                         else "pdf_snapshot_page_requires_review")
        answer = question.get("answer_source_range")
        if answer is not None and source[answer[0]:answer[0] + 1] not in {"\n", "\r"} and source[:answer[0]].rsplit("\n", 1)[-1].strip():
            risks[i].add("answer_embedded_in_question")
    for row in dependency.get("question_risks", []):
        index = by_identifier.get(row.get("id"))
        if index is None:
            return original("dependency_identity_unknown")
        risks[index].add("cross_question_dependency")
    if source_basis == "first_pass_transcription":
        view = _mask_literals(source, tex_comments=False)
        markers = list(source_metadata._SOURCE_RISK.finditer(view))
        markers.extend(re.finditer(r"\[插图待补[^\]\n]*\]", view))
        for match in markers:
            members = [i for i, owned in enumerate(owned_by_question)
                       if any(a <= match.start() < match.end() <= b for a, b in owned)]
            if len(members) != 1:
                return original("pdf_source_marker_ownership_unknown")
            risks[members[0]].add("pdf_first_pass_source_marker")
    for figure in figures:
        owners = figure["owners"]
        if len(owners) > 1:
            join(owners, "shared_pdf_figure")
        if figure["risk"]:
            for index in owners:
                risks[index].add("pdf_figure_requires_review")
        if any(figure["page_index"] not in {page["page_index"] for page in question_pages[i]} for i in owners):
            join(owners, "pdf_figure_cross_page_ownership")
    grouped = defaultdict(list)
    for i in range(len(questions)):
        grouped[find(i)].append(i)
    bundles = []
    for members in sorted(grouped.values(), key=lambda values: values[0]):
        clean = not any(risks[i] for i in members)
        if (bundles and clean and not any(risks[i] for i in bundles[-1]) and bundles[-1][-1] + 1 == members[0]):
            bundles[-1].extend(members)
        else:
            bundles.append(list(members))
    if len(bundles) > MAX_GROUPS:
        return original("pdf_hybrid_group_limit")
    try:
        if source_basis == "native_pdf":
            from mathbank.pdf_source_metadata import prepare_pdf_source_group as prepare_group
        else:
            from mathbank.pdf_source_metadata import prepare_pdf_transcription_group as prepare_group
    except ImportError:
        return original("pdf_group_certificate_interface_unavailable")
    for members in bundles:
        owned = _ranges([bounds for i in members for bounds in owned_by_question[i]], source)
        qids = [identifiers[i] for i in members]
        reasons = sorted(set().union(*(risks[i] for i in members)))
        group_id = "PHG_" + _sha(_json([plan["source_sha256"], task_id, generation, qids, owned]))[:24]
        group = {"id": group_id, "source_numbers": [questions[i]["source_number"] for i in members],
            "question_ids": qids, "owned_ranges": owned, "context_ranges": deepcopy(contexts),
            "source_range": [owned[0][0], owned[-1][1]],
            "page_numbers": sorted({page["page_number"] for i in members for page in question_pages[i]}),
            "figure_ids": [figure["id"] for figure in figures if set(members).intersection(figure["owners"])],
            "route": "split", "reasons": reasons, "scope_sha256": _sha(_json([qids, owned, contexts]))}
        group.update(source_basis=source_basis, native_reliable=False)
        if not reasons:
            try:
                group["_source_metadata_plan"] = prepare_group(source, diagnostics,
                    owned_ranges=owned, context_ranges=contexts, group_id=group_id,
                    task_id=task_id, generation=generation, source_document_sha256=source_document_sha256,
                    source_review_evidence=source_review_evidence, source_pages=source_pages,
                    layout_result=layout_result)
                group["route"] = "metadata"
                _require_group(plan, group)
                group["native_reliable"] = source_basis == "native_pdf"
            except Exception:
                group.pop("_source_metadata_plan", None)
                group["route"] = "split"; group["reasons"] = ["pdf_source_group_not_certified"]
        plan["groups"].append(group)
    native = [group for group in plan["groups"] if group["route"] == "metadata"]
    if not native:
        return original("no_certified_native_groups")
    reused = sum(right - left for group in native for left, right in group["owned_ranges"])
    if reused < min_metadata_source_characters:
        return original("native_reuse_below_character_policy")
    total_owned = sum(right - left for group in plan["groups"] for left, right in group["owned_ranges"])
    plan["reuse_policy"]["metadata_source_fraction"] = reused / total_owned
    if len(native) != len(plan["groups"]) and reused / total_owned < min_metadata_source_fraction:
        return original("metadata_reuse_below_mixed_character_fraction_policy")
    plan["mode"] = "whole_metadata" if len(native) == len(plan["groups"]) else "hybrid"
    plan["native_reliable"] = source_basis == "native_pdf"
    plan["request_plan"] = [{"kind": "pdf_hybrid", "max_posts": 1,
        "metadata_group_ids": [group["id"] for group in native],
        "split_group_ids": [group["id"] for group in plan["groups"] if group["route"] == "split"],
        "source_characters": len(source), "reused_native_characters": reused,
        "character_policy_is_token_measurement": False}]
    return _seal(plan)


def build_pdf_hybrid_plan(source: str, diagnostics: Mapping, **kwargs) -> dict:
    """Keep only independently proved native glyphs in the metadata path."""
    return _build_pdf_plan(source, diagnostics, source_basis="native_pdf", **kwargs)


def build_pdf_snapshot_hybrid_plan(source: str, diagnostics: Mapping, **kwargs) -> dict:
    """Conserve complete first-pass machine drafts; do not attest PDF truth."""
    return _build_pdf_plan(source, diagnostics, source_basis="first_pass_transcription", **kwargs)


def pdf_hybrid_plan_diagnostics(plan: dict) -> dict:
    """Content-free facts; private certificates and source remain server-only."""
    return {"schema": plan.get("schema"), "mode": plan.get("mode"),
        "source_basis": plan.get("source_basis"), "native_reliable": plan.get("native_reliable", False),
        "source_sha256": plan.get("source_sha256"), "document_sha256": plan.get("document_sha256"),
        "group_count": len(plan.get("groups", [])),
        "metadata_groups": sum(group.get("route") == "metadata" for group in plan.get("groups", [])),
        "split_groups": sum(group.get("route") == "split" for group in plan.get("groups", [])),
        "global_reasons": list(plan.get("global_reasons", [])),
        "reuse_policy": deepcopy(plan.get("reuse_policy", {})),
        "scope_proof_sha256": plan.get("scope_proof_sha256"),
        "primary_posts": 1 if plan.get("request_plan") else 0,
        "max_additional_posts": plan.get("fallback_policy", {}).get("max_additional_posts", 0)}


def pdf_snapshot_hybrid_plan_diagnostics(plan: dict) -> dict:
    """Describe retained machine drafts without upgrading their confidence."""
    return pdf_hybrid_plan_diagnostics(plan)
