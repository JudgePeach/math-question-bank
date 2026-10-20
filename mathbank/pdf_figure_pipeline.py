"""Integrate pixel review with immutable OCR and verified-only image anchors."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import time

from .ai_http import post_chat_completion
from .pdf_figure_review import SCHEMA, review_pdf_figures
from .pdf_figures import clear_resolved_figure_placeholders, describe_figure_slots
from .pdf_layout import (crop_pdf_figure, _anchor_positions, _protected_spans,
                         _inside_markdown_table)
from .task_manager import TaskCancelled


class ProjectFigureReviewCache:
    """Persist every dispatch/reply without keys or embedded request images."""
    def __init__(self, store, project_id, document_sha256, resolve_asset):
        self.store, self.project_id = store, project_id
        self.document_sha256, self.resolve_asset = document_sha256, resolve_asset

    def _binding(self, key):
        sha = hashlib.sha256(key.encode()).hexdigest()
        return "figure-review-" + sha, self.document_sha256 + ":" + sha

    def get(self, key):
        stage, fingerprint = self._binding(key)
        packet = self.store.load_stage(self.project_id, stage, fingerprint)
        if isinstance(packet, dict) and packet.get("image_path"):
            # Restore a retained project asset retired by an older task's TTL.
            try:
                packet["crop_path"] = str(self.resolve_asset(packet["image_path"]))
            except (OSError, ValueError):
                packet["crop_path"] = ""
        return packet

    def put(self, key, packet):
        stage, fingerprint = self._binding(key)
        self.store.save_stage(self.project_id, stage, packet, fingerprint, complete=True)


def _context(source, figure, slots):
    slot = next((row for row in slots if row["id"] == figure.get("slot_id")), None)
    if slot:
        return source[max(0, slot["start"]-1200):min(len(source), slot["end"]+600)]
    before, after = str(figure.get("anchor_before") or ""), str(figure.get("anchor_after") or "")
    return (before[-1800:] + "\n[此处配图]\n" + after[:1000])[:4000]


def _anchor_page(source, figures, pending):
    """Resolve all references against the unchanged original page, including gaps."""
    protected, inserts, attached = _protected_spans(source), {}, set()
    for number, figure in enumerate([*figures, *pending], 1):
        before, after = figure.get("anchor_before", ""), figure.get("anchor_after", "")
        if not (before.strip() or after.strip()):
            continue
        positions = _anchor_positions(source, before, after)
        if len(positions) != 1 or any(a < positions[0] < b for a, b in protected):
            continue
        position = positions[0]
        spacing = " " if _inside_markdown_table(source, position) else "\n"
        if figure in pending:
            # OCR already owns its exact unresolved slot; never add another one.
            if figure.get("slot_id") and any(slot["id"] == figure["slot_id"]
                    for slot in describe_figure_slots(source, figure["page_index"])):
                continue
            reference = f"[插图待补: 图{number}]"
        else:
            reference = f"![插图]({figure['image_path']})"
            attached.add(figure["id"])
        inserts.setdefault(position, []).append(spacing + reference + spacing)
    text = source
    for position in sorted(inserts, reverse=True):
        text = text[:position] + "".join(inserts[position]) + text[position:]
    text = clear_resolved_figure_placeholders(text, [row for row in figures if row["id"] in attached])
    return text, attached


def review_pdf_layout_figures(layout_result, source_texts, page_indices, page_images, *,
        file_bytes, output_dir, url_prefix, task_id, provider, cache=None,
        resolve_asset, register_asset=lambda _url: None, check_cancelled=lambda: None,
        progress=lambda _summary: None, post=post_chat_completion, semaphore=None,
        enabled=True, retry_pending=False):
    """Keep raw OCR immutable; only verified PNGs become final source references.

    Candidate layouts (including bad crops) remain separate evidence in the
    prepared checkpoint. A review exception preserves text and unresolved slots.
    Original-PDF recrops run on the caller thread, HTTP phases in bounded workers.
    """
    import pymupdf as fitz

    if not (len(source_texts) == len(page_indices) == len(page_images)):
        raise ValueError("PDF配图与首次识图页码不一致。")
    result = deepcopy(layout_result)
    pages, candidates = [], {}
    by_page = {page["page_index"]: page for page in result.get("pages", [])}
    for local, index in enumerate(page_indices):
        page = by_page.get(index)
        if page is None:
            continue
        slots = describe_figure_slots(source_texts[local], index)
        # A declared OCR slot with no usable candidate still needs localization.
        represented = {figure.get("slot_id") for figure in page.get("figures", [])}
        for slot in slots:
            if slot["id"] not in represented:
                page.setdefault("figures", []).append({"id": slot["id"] + "-missing",
                    "slot_id": slot["id"], "bbox": None, "anchor_before": source_texts[local][:slot["end"]],
                    "anchor_after": "", "page_index": index})
        inputs = []
        for figure in page.get("figures", []):
            row = deepcopy(figure)
            row["page_index"] = index
            row["slot_id"] = row.get("slot_id") or "native:" + row["id"]
            crop_path = None
            if row.get("image_path"):
                try:
                    crop_path = str(resolve_asset(row["image_path"]))
                except (OSError, ValueError):
                    pass
            candidates[row["id"]] = row
            inputs.append({"id": row["id"], "slot_id": row["slot_id"], "bbox": row.get("bbox"),
                "crop_path": crop_path, "image_path": row.get("image_path"),
                "context": _context(source_texts[local], row, slots)})
        if inputs:
            path = Path(page_images[local])
            pages.append({"page_number": index+1, "page_index": index, "source_path": str(path),
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "figures": inputs})

    started = time.monotonic()
    document_sha256 = hashlib.sha256(file_bytes).hexdigest()
    latest = {}
    def report(summary):
        latest.update(deepcopy(summary))
        snapshot = deepcopy(summary)
        for item in snapshot.get("items", []):
            item["status"] = {"accepted": "verified", "repaired": "corrected"}.get(item["status"], item["status"])
            item["reason"] = item.get("review", {}).get("reason", "")
        progress(snapshot)
    with fitz.open(stream=file_bytes, filetype="pdf") as document:
        def recrop(number, identifier, bbox):
            check_cancelled()
            suffix = hashlib.sha256(repr(bbox).encode()).hexdigest()[:12]
            url = crop_pdf_figure(document[number-1], bbox, Path(output_dir), url_prefix,
                f"pdf_review_{task_id}_{identifier}_{suffix}")
            register_asset(url)
            return {"crop_path": str(resolve_asset(url)), "image_path": url}

        try:
            packet = review_pdf_figures(pages, provider=provider, recrop=recrop, post=post,
                cache=cache, semaphore=semaphore, check_cancelled=check_cancelled,
                progress=report, retry_pending=retry_pending, document_sha256=document_sha256,
                max_calls=12 if enabled else 0)
            if not enabled:
                packet["summary"]["status"] = "disabled"
        except TaskCancelled:
            raise
        except Exception as exc:
            # Never turn a failed optional image review into lost question text.
            packet = {"figures": [], "summary": {**latest, "status": "unavailable",
                "calls": latest.get("calls", 0), "reason": type(exc).__name__}}

    receipts = {row["id"]: row for row in packet["figures"]}
    items, verified, corrected, pending_count = [], 0, 0, 0
    texts = list(source_texts)
    for local, index in enumerate(page_indices):
        page = by_page.get(index)
        if page is None:
            continue
        accepted, pending = [], []
        for original in page.get("figures", []):
            figure = deepcopy(candidates[original["id"]])
            receipt = receipts.get(figure["id"], {})
            status = receipt.get("status")
            reason = receipt.get("review", {}).get("reason", packet["summary"].get("reason", "not_reviewed"))
            if status in {"accepted", "repaired"}:
                url = receipt.get("image_path")
                try:
                    path = Path(resolve_asset(url))
                    valid = (path.is_file() and not path.is_symlink()
                        and hashlib.sha256(path.read_bytes()).hexdigest() == receipt.get("crop_sha256"))
                except (OSError, ValueError, TypeError):
                    valid = False
                positions = _anchor_positions(source_texts[local],figure.get("anchor_before", ""),
                    figure.get("anchor_after", ""))
                safe_anchor = len(positions) == 1 and not any(a < positions[0] < b
                    for a,b in _protected_spans(source_texts[local]))
                if valid and safe_anchor:
                    register_asset(url)
                    figure.update(image_path=url, bbox=receipt["bbox"], crop_review=deepcopy(receipt),
                        review_required=False, review_reasons=[])
                    if status == "repaired":
                        figure.update(native_box=False, candidate_ids=[])
                    accepted.append(figure)
                    verified += 1
                    corrected += status == "repaired"
                else:
                    status, reason = "pending", "anchor_unresolved" if valid else "reviewed_asset_changed"
            if status not in {"accepted", "repaired"}:
                figure["candidate_image_path"] = figure.pop("image_path", None)
                figure.update(attached=False, crop_review=deepcopy(receipt), review_required=True)
                pending.append(figure)
                pending_count += 1
            items.append({"id": figure["id"], "slot_id": figure["slot_id"], "page_number": index+1,
                "status": "corrected" if status == "repaired" else "verified" if status == "accepted" else "pending",
                "reason": "" if status in {"accepted", "repaired"} else reason})
        texts[local], attached = _anchor_page(source_texts[local], accepted, pending)
        for figure in accepted:
            figure["attached"] = figure["id"] in attached
        page.update(figures=accepted, pending_figures=pending,
            status="pending" if pending else "verified" if accepted else page.get("status", "skipped"))
    summary = deepcopy(packet["summary"])
    summary.update(schema=SCHEMA, total=len(items), verified=verified, corrected=corrected,
        pending=pending_count, items=items, elapsed_seconds=round(time.monotonic()-started, 3))
    if summary.get("status") not in {"disabled", "unavailable"}:
        summary["status"] = "pending" if pending_count else "verified"
    result.update(page_texts=texts, figure_review=summary, figure_review_schema=SCHEMA)
    result.setdefault("diagnostics", {}).update(figures_attached=sum(
        bool(f.get("attached")) for p in result.get("pages", []) for f in p.get("figures", [])),
        verified_figures=verified, pending_figures=pending_count)
    progress(deepcopy(summary))
    return result


def bind_figure_review_questions(summary, questions, *, source_matches=(), source_pages=()):
    """Bind a slot to its exact card field, rather than guessing from a page."""
    source, slots, cursor = "", {}, 0
    chunks = []
    for page in source_pages:
        number, text = page.get("page_number"), page.get("markdown")
        if type(number) is not int or number <= 0 or not isinstance(text, str):
            continue
        if not text.strip():
            continue
        value = re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->", "", text.strip())
        if chunks:
            cursor += 2
        page_slots = describe_figure_slots(value, number - 1)
        if page.get("fragments") is not None:
            # Canonical sources can replace slot1 with a verified PNG while
            # slot2 remains pending. Its original identity must not be renamed.
            from .pdf_fragment_sources import checked_fragment_page
            try:
                checked_fragment_page(page)
                original_slots = [slot for receipt in page.get("field_sources", [])
                                  for slot in receipt.get("slots", []) if slot.get("replaced") is False]
                if page.get("field_sources"):
                    leading = len(text) - len(text.lstrip())
                    page_slots = [{"id": slot["slot_id"], "label": slot["label"],
                        "start": len(re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->", "", text.strip()[:slot["range"][0]-leading])),
                        "end": len(re.sub(r"<!-- MATHBANK_PDF_PAGE:\d+ -->", "", text.strip()[:slot["range"][1]-leading]))}
                        for slot in original_slots]
            except (ValueError, KeyError, TypeError):
                page_slots = []
        for slot in page_slots:
            slots.setdefault(slot["id"], []).append({**slot,
                "page_number": number, "start": cursor + slot["start"], "end": cursor + slot["end"]})
        chunks.append(value)
        cursor += len(value)
    source = "\n\n".join(chunks)
    for item in summary.get("items", []):
        page, slot = item.get("page_number"), item.get("slot_id")
        # Discard stale derived targeting when rebinding another machine result.
        for name in ("question_index", "source_number", "target_field", "slot_label", "slot_offset"):
            item.pop(name, None)
        candidates = []
        owned = slots.get(slot, [])
        if len(owned) == 1 and owned[0]["page_number"] == page:
            atom = owned[0]
            for match in source_matches:
                index, field = match.get("question_index"), match.get("field")
                spans = match.get("source_ranges")
                if (type(index) is not int or not 0 <= index < len(questions)
                        or field not in {"content", "answer_markdown"}
                        or not isinstance(spans, list) or not spans
                        or any(not isinstance(span, list) or len(span) != 2
                            or any(type(n) is not int for n in span)
                            or not 0 <= span[0] < span[1] <= len(source) for span in spans)
                        or any(a[1] > b[0] for a, b in zip(spans, spans[1:]))):
                    continue
                text = "".join(source[a:b] for a, b in spans)
                if text != match.get("source_excerpt") or text != questions[index].get(field):
                    continue
                offset = 0
                for a, b in spans:
                    if a <= atom["start"] < atom["end"] <= b:
                        position = offset + atom["start"] - a
                        if (text[position:position + len(atom["label"])] == atom["label"]
                                and text.count(atom["label"]) == 1):
                            candidates.append((index, field, atom["label"], position))
                    offset += b - a
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) == 1:
            index, field, label, offset = candidates[0]
            item.update(question_index=index, source_number=questions[index].get("source_number"),
                        target_field=field, slot_label=label, slot_offset=offset)
            continue
        matched = []
        if type(page) is not int or page < 1:
            continue
        for index, question in enumerate(questions):
            figures = question.get("pdf_source_figures") or []
            if any(f.get("page_index") == page-1 and f.get("slot_id") == slot for f in figures):
                matched.append(index)
        if len(matched) == 1:
            item.update(question_index=matched[0], source_number=questions[matched[0]].get("source_number"))


_DERIVED_FIGURE_ATOM = re.compile(
    r"!\[插图\]\(/static/(?:uploads|test_uploads)/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.png\)"
    r"|\[插图待补(?:\s*[:：]\s*[^\]\n]{0,80})?\]"
)


def _figure_source_projection(text):
    """Ignore only owned figure atoms, retaining all other text and whitespace."""
    from .question_assets import markdown_literal_ranges
    literal = markdown_literal_ranges(text, include_links=False)
    protected = _protected_spans(text)
    chunks, atoms, boundaries, positions, cursor, count = [], [], set(), [], 0, 0
    for match in _DERIVED_FIGURE_ATOM.finditer(text):
        slash = match.start()-1
        while slash >= 0 and text[slash] == "\\":
            slash -= 1
        if (match.start()-slash-1) % 2:
            continue
        if any(a <= match.start() < b for a,b in literal):
            continue
        if any(a <= match.start() and match.end() <= b and (a,b) != match.span() for a,b in protected):
            continue
        piece = text[cursor:match.start()]
        chunks.append(piece)
        count += sum(not ch.isspace() for ch in piece)
        boundaries.add(count)
        positions.append(count)
        atoms.append(match.group())
        cursor = match.end()
    chunks.append(text[cursor:])
    visible, whitespace = [], {}
    for ch in "".join(chunks):
        if ch.isspace():
            whitespace[len(visible)] = whitespace.get(len(visible), "") + ch
        else:
            visible.append(ch)
    return "".join(visible), whitespace, boundaries, atoms, positions


def invalidate_figure_derivation(checkpoint, previous_source, current_source,
                                archive=lambda _packet, _signature: None):
    """Archive then invalidate derived split data for proved figure-only changes.

    Caller supplies the exact old prepared-page merge as previous_source. This
    helper does not rewrite OCR pages, prepared layout, figures, or question text.
    Production callers provide a durable archive callback; a failed callback or
    mismatched original binding leaves the checkpoint untouched.
    """
    import json
    if not isinstance(checkpoint, dict) or not isinstance(previous_source, str) or not isinstance(current_source, str):
        raise ValueError("配图派生来源格式无效。")
    if previous_source == current_source:
        return {"changed": False}
    previous = checkpoint.get("split_input")
    if previous is None:
        if checkpoint.get("accepted_split") is not None:
            raise ValueError("旧拆题结果缺少可确认的来源，未自动失效。")
        return {"changed": False}
    if not isinstance(previous, dict) or previous.get("source_markdown") != previous_source:
        raise ValueError("旧拆题来源与已保存页面不一致，未自动失效。")
    model = previous.get("model_source")
    if (not isinstance(model, str) or previous.get("model_source_sha256") != hashlib.sha256(model.encode("utf-8")).hexdigest()):
        raise ValueError("旧拆题模型来源哈希无效，未自动失效。")
    old_visible, old_space, old_edges, old_atoms, old_positions = _figure_source_projection(previous_source)
    new_visible, new_space, new_edges, new_atoms, new_positions = _figure_source_projection(current_source)
    def subsequence(smaller, larger):
        cursor = iter(larger)
        return all(any(candidate == position for candidate in cursor) for position in smaller)
    anchored = (subsequence(old_positions, new_positions) if len(old_positions) <= len(new_positions)
                else subsequence(new_positions, old_positions))
    if (old_visible != new_visible or old_atoms == new_atoms
            or not anchored
            or any(old_space.get(position, "") != new_space.get(position, "")
                   and position not in old_edges | new_edges for position in old_space.keys() | new_space.keys())):
        raise ValueError("来源变化不只是已确认的配图引用，未自动失效正文。")
    old_input = deepcopy(previous)
    had_accepted = "accepted_split" in checkpoint
    old_accepted = deepcopy(checkpoint.get("accepted_split"))
    packet = {"schema": "mathbank.pdf-figure-derivation-archive.v1", "split_input": old_input,
        "accepted_split": old_accepted, "accepted_split_present": had_accepted,
        "previous_source_sha256": hashlib.sha256(previous_source.encode("utf-8")).hexdigest(),
        "current_source_sha256": hashlib.sha256(current_source.encode("utf-8")).hexdigest()}
    signature = hashlib.sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    archive(deepcopy(packet), signature)
    if (checkpoint.get("split_input") != old_input or ("accepted_split" in checkpoint) != had_accepted
            or checkpoint.get("accepted_split") != old_accepted):
        raise ValueError("归档期间旧拆题来源发生变化，未自动失效。")
    checkpoint.pop("split_input", None)
    checkpoint.pop("accepted_split", None)
    return {"changed": True, "archive_sha256": signature,
        "previous_source_sha256": packet["previous_source_sha256"],
        "current_source_sha256": packet["current_source_sha256"]}
