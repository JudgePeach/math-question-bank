"""Indexed persistence and lookup for conservative question duplicate review.

The fingerprint table is rebuildable derived data.  This service keeps all
SQLAlchemy access and image hashing out of the pure comparison module so the
same deterministic rules can be tested without a database or filesystem.
"""

from __future__ import annotations

import hashlib
import json
import time
import datetime as dt
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from PIL import Image, ImageOps
from sqlalchemy import and_, func, literal, select, union_all
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mathbank.asset_security import resolve_upload_asset
from mathbank.database import Question, QuestionFingerprint as StoredFingerprint
from mathbank.question_duplicates import (
    EMPTY_TEXT_BAND,
    FINGERPRINT_VERSION,
    QuestionDuplicateInput,
    QuestionFingerprint,
    build_question_fingerprint,
    compare_question_fingerprints,
    find_batch_duplicate_groups,
)


MAX_RECALL_CANDIDATES = 300
SYNC_BACKFILL_LIMIT = 500


@dataclass(frozen=True)
class IndexedCandidate:
    question: Question
    fingerprint: QuestionFingerprint
    comparison: object


def _json_list(value: str | None) -> tuple[str, ...]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item or "") for item in parsed)


def _normalize_tikz_source(value: str) -> str:
    def strip_comment(raw_line: str) -> str:
        for index, char in enumerate(raw_line):
            if char != "%":
                continue
            slash_count = 0
            cursor = index - 1
            while cursor >= 0 and raw_line[cursor] == "\\":
                slash_count += 1
                cursor -= 1
            if slash_count % 2 == 0:
                return raw_line[:index]
        return raw_line

    lines = []
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = strip_comment(raw_line).strip()
        if line:
            lines.append(line)
    return " ".join(" ".join(lines).split())


def tikz_signatures(assets: object, legacy_tikz_code: str = "") -> tuple[str, ...]:
    values = assets if isinstance(assets, list) else []
    signatures: list[str] = []
    for asset in values:
        if not isinstance(asset, Mapping):
            continue
        normalized = _normalize_tikz_source(str(asset.get("tikz_code") or ""))
        if normalized:
            signatures.append(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    if not signatures:
        normalized = _normalize_tikz_source(legacy_tikz_code)
        if normalized:
            signatures.append(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    return tuple(signatures)


def select_visible_question_images(
    content: str,
    answer_markdown: str,
    image_paths: Iterable[str],
    content_tikz_assets: object,
) -> list[str]:
    """Keep prompt figures while excluding answer-only and AI reference assets."""

    paths = [str(path or "").strip() for path in image_paths if str(path or "").strip()]
    hidden_references: set[str] = set()
    assets = content_tikz_assets if isinstance(content_tikz_assets, list) else []
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        reference = str(asset.get("reference_image_path") or "").strip()
        if reference:
            hidden_references.add(reference)
    visible_candidates = [path for path in paths if path not in hidden_references]
    answer_only = {path for path in visible_candidates if path in str(answer_markdown or "")}
    # Legacy questions sometimes stored a prompt image only in image_paths.
    return [path for path in visible_candidates if path not in answer_only]


def select_answer_images(
    answer_markdown: str,
    image_paths: Iterable[str],
    answer_tikz_assets: object,
) -> list[str]:
    paths = [str(path or "").strip() for path in image_paths if str(path or "").strip()]
    assets = answer_tikz_assets if isinstance(answer_tikz_assets, list) else []
    rendered_paths: set[str] = set()
    hidden_references: set[str] = set()
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        rendered = str(asset.get("image_path") or "").strip()
        reference = str(asset.get("reference_image_path") or "").strip()
        if rendered:
            rendered_paths.add(rendered)
        if reference:
            hidden_references.add(reference)
    return [
        path
        for path in paths
        if path not in hidden_references
        and (path in str(answer_markdown or "") or path in rendered_paths)
    ]


@lru_cache(maxsize=512)
def _decoded_pixel_signature(
    path_value: str,
    modified_ns: int,
    file_size: int,
) -> str:
    # mtime and size are explicit cache-key fields; the path alone would keep a
    # stale hash when an asset is atomically replaced in place.
    del modified_ns, file_size
    with Image.open(path_value) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        digest = hashlib.sha256()
        digest.update(f"{image.width}x{image.height}:RGBA:".encode("ascii"))
        digest.update(image.tobytes())
        return digest.hexdigest()


def visible_image_signatures(
    references: Iterable[str],
    *,
    uploads_dir: str | Path,
    url_prefix: str,
) -> tuple[str, ...]:
    """Hash decoded pixels so filenames, EXIF and compression do not affect identity."""

    signatures: list[str] = []
    for reference in references:
        normalized_reference = str(reference or "").strip()
        if not normalized_reference:
            continue
        try:
            path = resolve_upload_asset(
                normalized_reference,
                uploads_dir=uploads_dir,
                url_prefix=url_prefix,
            )
            stat = path.stat()
            signatures.append(
                _decoded_pixel_signature(
                    str(path),
                    int(stat.st_mtime_ns),
                    int(stat.st_size),
                )
            )
        except Exception:
            # Preserve figure count while an empty signature forces the pure
            # comparator to report VISUAL_SIGNATURE_PENDING.
            signatures.append("")
    return tuple(signatures)


def duplicate_input_for_question(
    question: Question,
    *,
    uploads_dir: str | Path,
    url_prefix: str,
) -> QuestionDuplicateInput:
    display_paths = question.display_image_paths
    return QuestionDuplicateInput(
        content=question.content or "",
        answer_markdown=question.answer_markdown or "",
        question_type=question.question_type or "",
        visible_image_signatures=visible_image_signatures(
            select_visible_question_images(
                question.content or "",
                question.answer_markdown or "",
                display_paths,
                question.content_tikz_assets,
            ),
            uploads_dir=uploads_dir,
            url_prefix=url_prefix,
        ),
        tikz_signatures=tikz_signatures(
            question.content_tikz_assets,
            question.tikz_code or "",
        ),
        answer_asset_signatures=(
            visible_image_signatures(
                select_answer_images(
                    question.answer_markdown or "",
                    display_paths,
                    question.answer_tikz_assets,
                ),
                uploads_dir=uploads_dir,
                url_prefix=url_prefix,
            )
            + tikz_signatures(question.answer_tikz_assets)
        ),
    )


def _stored_values(fingerprint: QuestionFingerprint) -> dict[str, object]:
    values: dict[str, object] = {
        "content_revision_hash": fingerprint.content_revision_hash,
        "exact_hash": fingerprint.exact_hash,
        "critical_math_hash": fingerprint.critical_math_hash,
        "answer_hash": fingerprint.answer_hash,
        "simhash_hex": fingerprint.simhash_hex,
        "token_count": fingerprint.token_count,
        "choice_count": fingerprint.choice_count,
        "figure_count": fingerprint.figure_count,
        "visible_image_hashes": json.dumps(
            list(fingerprint.visible_image_signatures), ensure_ascii=False
        ),
        "tikz_hashes": json.dumps(list(fingerprint.tikz_signatures), ensure_ascii=False),
        "status": "ready",
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
    }
    for band_no, band_value in enumerate(fingerprint.bands):
        values[f"band{band_no}"] = band_value
    for band_no, band_value in enumerate(fingerprint.text_bands):
        values[f"text_band{band_no}"] = band_value
    return values


def upsert_question_fingerprint(
    db: Session,
    question: Question,
    fingerprint: QuestionFingerprint,
) -> None:
    # A row from an older algorithm may describe pre-edit content.  Keeping it
    # would make a code rollback trust a stale ready fingerprint, so every
    # content write retires other versions before storing the current one.
    db.query(StoredFingerprint).filter(
        StoredFingerprint.question_id == question.id,
        StoredFingerprint.fingerprint_version != fingerprint.fingerprint_version,
    ).delete(synchronize_session=False)
    values = {
        "question_id": question.id,
        "fingerprint_version": fingerprint.fingerprint_version,
        **_stored_values(fingerprint),
    }
    update_values = {
        field: value
        for field, value in values.items()
        if field not in {"question_id", "fingerprint_version"}
    }
    statement = sqlite_insert(StoredFingerprint).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=["question_id", "fingerprint_version"],
        set_=update_values,
    )
    db.execute(statement)


def fingerprint_for_question(
    question: Question,
    *,
    uploads_dir: str | Path,
    url_prefix: str,
) -> QuestionFingerprint:
    return build_question_fingerprint(
        duplicate_input_for_question(
            question,
            uploads_dir=uploads_dir,
            url_prefix=url_prefix,
        )
    )


def recall_question_ids(
    db: Session,
    fingerprint: QuestionFingerprint,
    *,
    exclude_id: int | None = None,
    limit: int = MAX_RECALL_CANDIDATES,
    diagnostics: dict[str, object] | None = None,
) -> list[int]:
    """Use the exact and eight band indexes; never scan question text."""

    length_floor = max(1, int(fingerprint.token_count * 0.55))
    length_ceiling = max(length_floor, int(fingerprint.token_count * 1.8) + 4)
    exact_query = db.query(StoredFingerprint.question_id).filter(
        StoredFingerprint.fingerprint_version == FINGERPRINT_VERSION,
        StoredFingerprint.exact_hash == fingerprint.exact_hash,
    )
    if exclude_id is not None:
        exact_query = exact_query.filter(
            StoredFingerprint.question_id != int(exclude_id)
        )
    safe_limit = max(1, min(int(limit), 1000))
    exact_rows = exact_query.limit(safe_limit + 1).all()
    exact_truncated = len(exact_rows) > safe_limit
    exact_ids = [row[0] for row in exact_rows[:safe_limit]]
    if len(exact_ids) >= safe_limit:
        if diagnostics is not None:
            diagnostics.update(
                {
                    "truncated_band_count": int(exact_truncated),
                    "exact_truncated": exact_truncated,
                    "dropped_by_global_budget": int(exact_truncated),
                    "index_complete": not exact_truncated,
                }
            )
        return exact_ids

    band_selects = []
    for band_no, band_value in enumerate(fingerprint.bands):
        conditions = [
            StoredFingerprint.fingerprint_version == FINGERPRINT_VERSION,
            getattr(StoredFingerprint, f"band{band_no}") == band_value,
            StoredFingerprint.token_count.between(length_floor, length_ceiling),
        ]
        if exclude_id is not None:
            conditions.append(StoredFingerprint.question_id != int(exclude_id))
        bounded_band = (
            select(
                StoredFingerprint.question_id.label("question_id"),
                literal(band_no).label("band_no"),
            )
            .where(*conditions)
            .limit(safe_limit + 1)
            .subquery(f"duplicate_band_{band_no}")
        )
        band_selects.append(
            select(bounded_band.c.question_id, bounded_band.c.band_no)
        )
    union_rows = db.execute(union_all(*band_selects)).all()
    per_band_count: Counter[int] = Counter()
    hit_count: Counter[int] = Counter()
    for question_id, band_no in union_rows:
        per_band_count[int(band_no)] += 1
        if per_band_count[int(band_no)] <= safe_limit:
            hit_count[int(question_id)] += 1
    truncated_band_count = sum(
        count > safe_limit for count in per_band_count.values()
    )
    exact_id_set = set(exact_ids)
    ranked_new_hits = [
        item
        for item in sorted(hit_count.items(), key=lambda item: (-item[1], item[0]))
        if item[0] not in exact_id_set
    ]
    available_slots = max(0, safe_limit - len(exact_ids))
    dropped_by_global_budget = max(0, len(ranked_new_hits) - available_slots)
    near_rows = ranked_new_hits[:available_slots]
    incomplete_signal_count = (
        truncated_band_count
        + int(exact_truncated)
        + int(dropped_by_global_budget > 0)
    )
    if diagnostics is not None:
        diagnostics.update(
            {
                "truncated_band_count": incomplete_signal_count,
                "exact_truncated": exact_truncated,
                "dropped_by_global_budget": dropped_by_global_budget,
                "index_complete": incomplete_signal_count == 0,
            }
        )

    # Preserve every exact hit first, then add the strongest LSH candidates.
    result = list(exact_ids)
    seen = set(result)
    for question_id, _hit_count in near_rows:
        if question_id not in seen:
            seen.add(question_id)
            result.append(question_id)
        if len(result) >= safe_limit:
            break
    return result


def recall_text_fragment_question_ids(
    db: Session,
    fingerprint: QuestionFingerprint,
    *,
    exclude_id: int | None = None,
    exclude_ids: Iterable[int] = (),
    limit: int = MAX_RECALL_CANDIDATES,
    diagnostics: dict[str, object] | None = None,
) -> list[int]:
    """Recall bounded substring candidates through eight text-fragment indexes."""

    safe_limit = max(1, min(int(limit), 1000))
    excluded = {int(value) for value in exclude_ids}
    if exclude_id is not None:
        excluded.add(int(exclude_id))
    length_floor = max(1, int(fingerprint.token_count * 0.55))
    length_ceiling = max(length_floor, int(fingerprint.token_count * 1.8) + 4)
    band_selects = []
    for band_no, band_value in enumerate(fingerprint.text_bands):
        if not band_value or band_value == EMPTY_TEXT_BAND:
            continue
        conditions = [
            StoredFingerprint.fingerprint_version == FINGERPRINT_VERSION,
            getattr(StoredFingerprint, f"text_band{band_no}") == band_value,
            StoredFingerprint.token_count.between(length_floor, length_ceiling),
        ]
        if excluded:
            conditions.append(StoredFingerprint.question_id.notin_(excluded))
        bounded_band = (
            select(
                StoredFingerprint.question_id.label("question_id"),
                literal(band_no).label("band_no"),
            )
            .where(*conditions)
            .limit(safe_limit + 1)
            .subquery(f"duplicate_text_band_{band_no}")
        )
        band_selects.append(
            select(bounded_band.c.question_id, bounded_band.c.band_no)
        )

    if not band_selects:
        if diagnostics is not None:
            diagnostics.update(
                {"text_truncated_band_count": 0, "text_index_complete": True}
            )
        return []

    union_rows = db.execute(union_all(*band_selects)).all()
    per_band_count: Counter[int] = Counter()
    hit_count: Counter[int] = Counter()
    for question_id, band_no in union_rows:
        per_band_count[int(band_no)] += 1
        if per_band_count[int(band_no)] <= safe_limit:
            hit_count[int(question_id)] += 1
    truncated_band_count = sum(
        count > safe_limit for count in per_band_count.values()
    )
    ranked_hits = sorted(hit_count.items(), key=lambda item: (-item[1], item[0]))
    dropped_by_global_budget = max(0, len(ranked_hits) - safe_limit)
    incomplete_signal_count = truncated_band_count + int(
        dropped_by_global_budget > 0
    )
    if diagnostics is not None:
        diagnostics.update(
            {
                "text_truncated_band_count": incomplete_signal_count,
                "text_dropped_by_global_budget": dropped_by_global_budget,
                "text_index_complete": incomplete_signal_count == 0,
            }
        )
    return [
        question_id
        for question_id, _hit_count in ranked_hits[:safe_limit]
    ]


def _rank_indexed_candidate_ids(
    db: Session,
    fingerprint: QuestionFingerprint,
    question_ids: Sequence[int],
    *,
    uploads_dir: str | Path,
    url_prefix: str,
    limit: int,
    fingerprint_cache: dict[int, QuestionFingerprint] | None,
) -> list[IndexedCandidate]:
    if not question_ids:
        return []
    questions = db.query(Question).filter(Question.id.in_(question_ids)).all()
    ranked: list[IndexedCandidate] = []
    rank = {"exact": 3, "probable": 2, "possible_variant": 1, "none": 0}
    for question in questions:
        candidate_fingerprint = (
            fingerprint_cache.get(question.id) if fingerprint_cache is not None else None
        )
        if candidate_fingerprint is None:
            candidate_fingerprint = fingerprint_for_question(
                question,
                uploads_dir=uploads_dir,
                url_prefix=url_prefix,
            )
            if fingerprint_cache is not None:
                fingerprint_cache[question.id] = candidate_fingerprint
        comparison = compare_question_fingerprints(fingerprint, candidate_fingerprint)
        if comparison.classification == "none":
            continue
        ranked.append(IndexedCandidate(question, candidate_fingerprint, comparison))
    ranked.sort(
        key=lambda item: (
            -rank[item.comparison.classification],
            -item.comparison.score,
            item.question.id,
        )
    )
    return ranked[:limit]


def find_indexed_candidates(
    db: Session,
    fingerprint: QuestionFingerprint,
    *,
    uploads_dir: str | Path,
    url_prefix: str,
    exclude_id: int | None = None,
    limit: int = 5,
    fingerprint_cache: dict[int, QuestionFingerprint] | None = None,
    diagnostics: dict[str, object] | None = None,
) -> list[IndexedCandidate]:
    result_limit = max(1, min(int(limit), 20))
    working_cache = fingerprint_cache if fingerprint_cache is not None else {}
    shared_budget = max(
        20,
        min(MAX_RECALL_CANDIDATES, result_limit * 8),
    )
    primary_budget = (shared_budget + 1) // 2
    primary_diagnostics: dict[str, object] = {}
    primary_ids = recall_question_ids(
        db,
        fingerprint,
        exclude_id=exclude_id,
        limit=primary_budget,
        diagnostics=primary_diagnostics,
    )
    primary_ranked = _rank_indexed_candidate_ids(
        db,
        fingerprint,
        primary_ids,
        uploads_dir=uploads_dir,
        url_prefix=url_prefix,
        limit=result_limit,
        fingerprint_cache=working_cache,
    )
    if diagnostics is not None:
        diagnostics.update(primary_diagnostics)
        diagnostics.update(
            {
                "candidate_budget": shared_budget,
                "primary_candidate_count": len(primary_ids),
                "text_candidate_count": 0,
                "text_fallback_used": False,
                "text_truncated_band_count": 0,
                "text_index_complete": True,
            }
        )
    if primary_ranked:
        return primary_ranked

    remaining_budget = max(0, shared_budget - len(primary_ids))
    if remaining_budget == 0:
        return []
    text_diagnostics: dict[str, object] = {}
    text_ids = recall_text_fragment_question_ids(
        db,
        fingerprint,
        exclude_id=exclude_id,
        exclude_ids=primary_ids,
        limit=remaining_budget,
        diagnostics=text_diagnostics,
    )
    if diagnostics is not None:
        primary_truncated = int(
            primary_diagnostics.get("truncated_band_count") or 0
        )
        text_truncated = int(
            text_diagnostics.get("text_truncated_band_count") or 0
        )
        diagnostics.update(text_diagnostics)
        diagnostics.update(
            {
                "truncated_band_count": primary_truncated + text_truncated,
                "index_complete": bool(
                    primary_diagnostics.get("index_complete", True)
                    and text_diagnostics.get("text_index_complete", True)
                ),
                "text_candidate_count": len(text_ids),
                "text_fallback_used": True,
            }
        )
    return _rank_indexed_candidate_ids(
        db,
        fingerprint,
        text_ids,
        uploads_dir=uploads_dir,
        url_prefix=url_prefix,
        limit=result_limit,
        fingerprint_cache=working_cache,
    )


def exact_duplicate_ids(
    db: Session,
    fingerprint: QuestionFingerprint,
    *,
    exclude_id: int | None = None,
) -> list[int]:
    query = db.query(StoredFingerprint.question_id).filter(
        StoredFingerprint.fingerprint_version == FINGERPRINT_VERSION,
        StoredFingerprint.exact_hash == fingerprint.exact_hash,
    )
    if exclude_id is not None:
        query = query.filter(StoredFingerprint.question_id != int(exclude_id))
    return [row[0] for row in query.limit(50).all()]


def index_status(db: Session) -> dict[str, object]:
    total = int(db.query(func.count(Question.id)).scalar() or 0)
    indexed = int(
        db.query(func.count(StoredFingerprint.question_id))
        .filter(StoredFingerprint.fingerprint_version == FINGERPRINT_VERSION)
        .scalar()
        or 0
    )
    coverage = 1.0 if total == 0 else min(1.0, indexed / total)
    return {
        "ready": indexed >= total,
        "indexed": indexed,
        "total": total,
        "coverage": round(coverage, 6),
        "fingerprint_version": FINGERPRINT_VERSION,
    }


def backfill_missing_fingerprints(
    db: Session,
    *,
    uploads_dir: str | Path,
    url_prefix: str,
    limit: int = SYNC_BACKFILL_LIMIT,
    after_id: int = 0,
    return_cursor: bool = False,
) -> int | tuple[int, int]:
    missing = (
        db.query(Question)
        .outerjoin(
            StoredFingerprint,
            and_(
                StoredFingerprint.question_id == Question.id,
                StoredFingerprint.fingerprint_version == FINGERPRINT_VERSION,
            ),
        )
        .filter(
            Question.id > max(0, int(after_id)),
            StoredFingerprint.question_id.is_(None),
        )
        .order_by(Question.id.asc())
        .limit(max(1, min(int(limit), 2000)))
        .all()
    )
    for question in missing:
        fingerprint = fingerprint_for_question(
            question,
            uploads_dir=uploads_dir,
            url_prefix=url_prefix,
        )
        upsert_question_fingerprint(db, question, fingerprint)
    if missing:
        db.commit()
    last_id = missing[-1].id if missing else max(0, int(after_id))
    result = (len(missing), last_id)
    return result if return_cursor else result[0]


def rebuild_all_missing_fingerprints(
    session_factory,
    *,
    uploads_dir: str | Path,
    url_prefix: str,
    batch_size: int = 250,
) -> dict[str, object]:
    """Low-priority, resumable startup backfill with short write transactions."""

    indexed = 0
    last_question_id = 0
    while True:
        count = 0
        next_question_id = last_question_id
        for retry in range(3):
            db = session_factory()
            try:
                count, next_question_id = backfill_missing_fingerprints(
                    db,
                    uploads_dir=uploads_dir,
                    url_prefix=url_prefix,
                    limit=batch_size,
                    after_id=last_question_id,
                    return_cursor=True,
                )
                break
            except OperationalError:
                db.rollback()
                if retry == 2:
                    raise
                time.sleep(0.1 * (retry + 1))
            finally:
                db.close()
        indexed += count
        last_question_id = next_question_id
        if count < batch_size:
            break
        time.sleep(0.01)
    db = session_factory()
    try:
        status = index_status(db)
    finally:
        db.close()
    return {**status, "backfilled": indexed}


def batch_local_matches(
    fingerprints: Sequence[QuestionFingerprint],
) -> tuple[dict[int, list[dict[str, object]]], dict[str, object]]:
    scan = find_batch_duplicate_groups(fingerprints)
    matches: dict[int, list[dict[str, object]]] = {
        index: [] for index in range(len(fingerprints))
    }
    for group in scan.groups:
        for pair in group.comparisons:
            payload = pair.comparison.to_dict()
            matches[pair.left_index].append(
                {**payload, "other_index": pair.right_index}
            )
            matches[pair.right_index].append(
                {**payload, "other_index": pair.left_index}
            )
    diagnostics = {
        "index_complete": scan.index_complete,
        "candidate_pair_count": scan.candidate_pair_count,
        "compared_pair_count": scan.compared_pair_count,
        "truncated_bucket_count": scan.truncated_bucket_count,
        "dropped_candidate_pair_count": scan.dropped_candidate_pair_count,
    }
    return matches, diagnostics
