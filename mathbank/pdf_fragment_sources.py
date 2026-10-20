"""Stable, exact-range PDF fragment sources and verified-only image slots.

Raw page packets are never changed. This adapter derives one canonical page
string from its blocks, replacing only a uniquely owned verified PNG slot.
It performs no PDF rendering, OCR, model calls, or question-boundary scanning.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .asset_security import resolve_upload_asset
from .paths import UPLOADS_DIR
from .pdf_figures import describe_figure_slots
from .pdf_page_fragments import SCHEMA, ROLES, EDGES, MAX_BLOCKS, MAX_QUESTION_NUMBER

MAX_SOURCE = 500_000
_SHA = re.compile(r"^[a-f0-9]{64}$")


def text_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def checked_fragment_page(page):
    """Revalidate source/range identities, including accepted canonical pages.

    A missing fragment sidecar is an explicit manual/v1 page, not permission
    to guess blocks. A present but inconsistent sidecar is a hard local error.
    """
    if (not isinstance(page, dict) or type(page.get("page_number")) is not int
            or page["page_number"] < 1 or not isinstance(page.get("markdown"), str)):
        raise ValueError("PDF 片段页来源格式无效。")
    markdown = page["markdown"]
    if len(markdown) > 50_000:
        raise ValueError("PDF 片段页超过本地来源上限。")
    metadata = page.get("fragments")
    if metadata is None:
        return None
    if (not isinstance(metadata, dict) or metadata.get("schema") != SCHEMA
            or metadata.get("page_index") != page["page_number"] - 1
            or type(metadata.get("page_index")) is not int
            or metadata.get("source_sha256") != text_sha(markdown)
            or not isinstance(metadata.get("blocks"), list)
            or len(metadata["blocks"]) > MAX_BLOCKS
            or not isinstance(metadata.get("slot_blocks"), dict)):
        raise ValueError("PDF 片段页来源、页码或哈希不一致。")
    cursor, identifiers = 0, set()
    for block in metadata["blocks"]:
        if (not isinstance(block, dict) or set(block) != {"id", "role", "number", "edge", "range",
                                                       "text_sha256", "pending_reasons"}):
            raise ValueError("PDF 片段来源记录无效。")
        bounds = block.get("range")
        identifier = block.get("id")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 160
                or identifier in identifiers or not isinstance(bounds, list) or len(bounds) != 2
                or any(type(value) is not int for value in bounds)
                or bounds[0] != cursor or not cursor < bounds[1] <= len(markdown)
                or block.get("text_sha256") != text_sha(markdown[slice(*bounds)])
                or block.get("role") not in ROLES or block.get("edge") not in EDGES
                or (block.get("number") is not None
                    and (type(block["number"]) is not int or not 1 <= block["number"] <= MAX_QUESTION_NUMBER))
                or not isinstance(block.get("pending_reasons"), list)
                or len(block["pending_reasons"]) > 32
                or any(not isinstance(value, str) or not value or len(value) > 160
                       for value in block["pending_reasons"])):
            raise ValueError("PDF 片段范围、文字哈希或身份不一致。")
        cursor = bounds[1]
        identifiers.add(identifier)
    if cursor != len(markdown):
        raise ValueError("PDF 片段未完整覆盖本页原文。")
    slots = describe_figure_slots(markdown, page["page_number"] - 1)
    actual = {}
    for slot in slots:
        owners = [block for block in metadata["blocks"]
                  if block["range"][0] <= slot["start"] < slot["end"] <= block["range"][1]]
        if len(owners) != 1:
            raise ValueError("PDF 配图占位跨片段或无法唯一归属。")
        actual[slot["id"]] = owners[0]["id"]
    # Removing slot1 must not rename the unresolved original slot2. Derived
    # slot ranges keep original page identities independently of new ordinals.
    expected_slots = actual
    if page.get("field_sources"):
        expected_slots, unresolved_ranges = {}, set()
        by_id = {block["id"]: block for block in metadata["blocks"]}
        for receipt in page["field_sources"]:
            if not isinstance(receipt, dict) or receipt.get("block_id") not in by_id:
                raise ValueError("PDF 片段派生回执无效。")
            owner = by_id[receipt["block_id"]]
            for slot in receipt.get("slots", []):
                bounds = slot.get("range") if isinstance(slot, dict) else None
                identifier = slot.get("slot_id") if isinstance(slot, dict) else None
                if (not isinstance(identifier, str) or identifier in expected_slots
                        or not re.fullmatch(r"p" + str(page["page_number"]) + r"-s[1-9][0-9]*", identifier)
                        or not isinstance(bounds, list) or len(bounds) != 2
                        or any(type(value) is not int for value in bounds)
                        or not owner["range"][0] <= bounds[0] < bounds[1] <= owner["range"][1]):
                    raise ValueError("PDF 片段配图范围回执无效。")
                text = markdown[slice(*bounds)]
                if slot.get("replaced") is True:
                    changes = [change for change in receipt.get("replacements", [])
                               if change.get("slot_id") == identifier and change.get("range") == bounds]
                    if len(changes) != 1 or text != "![插图](" + str(changes[0].get("image_path")) + ")":
                        raise ValueError("PDF 片段已核验插图回执不一致。")
                elif slot.get("replaced") is False:
                    if text != slot.get("label") or len(describe_figure_slots(text, page["page_number"] - 1)) != 1:
                        raise ValueError("PDF 片段待补图位已变化。")
                    unresolved_ranges.add(tuple(bounds))
                else:
                    raise ValueError("PDF 片段配图回执状态无效。")
                expected_slots[identifier] = owner["id"]
        if unresolved_ranges != {(slot["start"], slot["end"]) for slot in slots}:
            raise ValueError("PDF 片段未完整登记待补图位。")
    if (metadata["slot_blocks"] != expected_slots
            or any(owner not in identifiers for owner in expected_slots.values())):
        raise ValueError("PDF 配图占位与片段归属回执不一致。")
    return metadata


def _default_resolve(url):
    return resolve_upload_asset(url, uploads_dir=UPLOADS_DIR, url_prefix="static/uploads")


def _verified_png(figure, page_index, resolve_asset):
    receipt = figure.get("crop_review")
    url = figure.get("image_path")
    if (figure.get("attached") is not True or figure.get("page_index", page_index) != page_index
            or figure.get("review_required") is True or not isinstance(receipt, dict)
            or receipt.get("status") not in {"accepted", "repaired"}
            or not isinstance(url, str) or not url.startswith("/static/uploads/")
            or not url.endswith(".png") or receipt.get("image_path") != url
            or not isinstance(receipt.get("crop_sha256"), str)
            or not _SHA.fullmatch(receipt["crop_sha256"])):
        return False
    try:
        # Central path validation is mandatory even for a supplied restore hook.
        resolve_upload_asset(url, uploads_dir=UPLOADS_DIR, url_prefix="static/uploads", require_file=False)
        path = Path(resolve_asset(url))
        if path.is_symlink() or not path.is_file() or not 8 <= path.stat().st_size <= 10 * 1024 * 1024:
            return False
        data = path.read_bytes()
        return data.startswith(b"\x89PNG\r\n\x1a\n") and hashlib.sha256(data).hexdigest() == receipt["crop_sha256"]
    except (ValueError, OSError, TypeError):
        return False


def prepare_pdf_fragment_sources(raw_pages, layout_result=None, *, resolve_asset=None):
    """Return {source, page_texts, source_pages, diagnostics}, without mutation.

    Each raw page is {page_number, markdown: joint.markdown, fragments}. Empty
    server-confirmed pages use the same schema with zero blocks and empty SHA.
    The only body edit is a verified PNG replacing its one original slot.
    """
    if not isinstance(raw_pages, list) or not 1 <= len(raw_pages) <= 80:
        raise ValueError("PDF 片段页清单无效。")
    numbers = [page.get("page_number") if isinstance(page, dict) else None for page in raw_pages]
    if (any(type(number) is not int or number < 1 for number in numbers)
            or numbers != sorted(set(numbers))):
        raise ValueError("PDF 片段物理页顺序重复或无效。")
    if any(not isinstance(page.get("markdown"), str) for page in raw_pages):
        raise ValueError("PDF 片段页原文必须是字符串。")
    if sum(len(page.get("markdown", "")) for page in raw_pages) > MAX_SOURCE:
        raise ValueError("PDF 片段来源总长超过本地保存上限。")
    layout_pages = {}
    for page in (layout_result or {}).get("pages", []):
        index = page.get("page_index")
        if type(index) is not int or index in layout_pages:
            raise ValueError("PDF 配图页面身份重复或无效。")
        layout_pages[index] = page
    resolve_asset = resolve_asset or _default_resolve
    pages, issues, replaced_count = [], [], 0
    for raw in raw_pages:
        metadata = checked_fragment_page(raw)
        if metadata is None:
            pages.append({"page_number": raw["page_number"], "markdown": raw["markdown"],
                          "fragments": None, "field_sources": []})
            issues.append({"page_number": raw["page_number"], "reason": "fragment_metadata_missing"})
            continue
        page_index = metadata["page_index"]
        raw_text = raw["markdown"]
        slot_map = {slot["id"]: slot for slot in describe_figure_slots(raw_text, page_index)}
        figures = {}
        for figure in layout_pages.get(page_index, {}).get("figures", []):
            figures.setdefault(figure.get("slot_id"), []).append(figure)
        edits = {}
        for slot_id, slot in slot_map.items():
            candidates = figures.get(slot_id, [])
            if len(candidates) == 1 and _verified_png(candidates[0], page_index, resolve_asset):
                edits[slot_id] = candidates[0]
            elif layout_result is not None:
                issues.append({"page_number": page_index + 1, "slot_id": slot_id,
                               "reason": "figure_not_verified_or_ambiguous"})
        chunks, blocks, receipts, cursor = [], [], [], 0
        for original in metadata["blocks"]:
            left, right = original["range"]
            text = raw_text[left:right]
            owned = sorted((slot for slot in slot_map.values()
                            if metadata["slot_blocks"][slot["id"]] == original["id"]
                            and slot["id"] in edits), key=lambda slot: slot["start"])
            pieces, changes, position, local = [], [], left, 0
            for slot in owned:
                before = raw_text[position:slot["start"]]
                figure = edits[slot["id"]]
                replacement = "![插图](" + figure["image_path"] + ")"
                pieces.extend([before, replacement])
                local += len(before)
                changes.append({"slot_id": slot["id"], "raw_range": [slot["start"], slot["end"]],
                    "range": [cursor + local, cursor + local + len(replacement)],
                    "image_path": figure["image_path"], "crop_sha256": figure["crop_review"]["crop_sha256"]})
                local += len(replacement)
                position = slot["end"]
            pieces.append(raw_text[position:right])
            canonical = "".join(pieces) if changes else text
            updated = deepcopy(original)
            updated.update(range=[cursor, cursor + len(canonical)], text_sha256=text_sha(canonical))
            blocks.append(updated)
            stable_slots = []
            for slot in slot_map.values():
                if metadata["slot_blocks"][slot["id"]] != original["id"]:
                    continue
                preceding_delta = sum((change["range"][1] - change["range"][0])
                    - (change["raw_range"][1] - change["raw_range"][0])
                    for change in changes if change["raw_range"][1] <= slot["start"])
                begin = cursor + slot["start"] - left + preceding_delta
                change = next((value for value in changes if value["slot_id"] == slot["id"]), None)
                stable_slots.append({"slot_id": slot["id"], "label": slot["label"],
                    "raw_range": [slot["start"], slot["end"]],
                    "range": list(change["range"]) if change else [begin, begin + slot["end"] - slot["start"]],
                    "replaced": change is not None})
            receipts.append({"block_id": original["id"], "raw_page_sha256": metadata["source_sha256"],
                "raw_range": [left, right], "raw_text_sha256": original["text_sha256"],
                "range": list(updated["range"]), "text_sha256": updated["text_sha256"],
                "replacements": changes, "slots": stable_slots})
            chunks.append(canonical)
            cursor += len(canonical)
            replaced_count += len(changes)
        text = "".join(chunks)
        fragments = {"schema": SCHEMA, "page_index": page_index, "source_sha256": text_sha(text),
                     "blocks": blocks, "slot_blocks": deepcopy(metadata["slot_blocks"])}
        page = {"page_number": page_index + 1, "markdown": text, "fragments": fragments,
                "field_sources": receipts}
        checked_fragment_page(page)
        pages.append(page)
    texts = [page["markdown"] for page in pages]
    return {"source": "\n\n".join(text.strip() for text in texts if text.strip()),
            "page_texts": texts, "source_pages": pages,
            "diagnostics": {"schema": SCHEMA, "pages": len(pages), "replaced_figures": replaced_count,
                            "issues": issues}}


def invalidate_pdf_fragment_derivation(checkpoint, raw_pages, previous_layout, current_layout, *,
                                       resolve_asset, archive):
    """Invalidate only a proved canonical-figure change in the same raw blocks.

    Unlike the legacy text scanner, raw block ownership survives a stray math
    delimiter in another block. Both layouts must still prove every claimed
    accepted PNG, and the previous exact C model input must be intact. Archive
    failure or concurrent mutation preserves the checkpoint in full.
    """
    if not isinstance(checkpoint, dict) or not callable(resolve_asset) or not callable(archive):
        raise ValueError("PDF 片段派生缓存接口无效。")

    def digest(value):
        return text_sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False))

    raw_snapshot = deepcopy(raw_pages)
    layout_snapshots = [deepcopy(previous_layout), deepcopy(current_layout)]

    def proved_bundle(layout):
        for page in (layout or {}).get("pages", []):
            for figure in page.get("figures", []):
                receipt = figure.get("crop_review") or {}
                if (figure.get("attached") is True and receipt.get("status") in {"accepted", "repaired"}
                        and not _verified_png(figure, page.get("page_index"), resolve_asset)):
                    raise ValueError("已核验配图的实际文件、哈希或回执已变化，未失效旧题稿。")
        return prepare_pdf_fragment_sources(raw_snapshot, layout, resolve_asset=resolve_asset)

    before, after = [proved_bundle(layout) for layout in layout_snapshots]
    if before["source"] == after["source"]:
        return {"changed": False, "protocol": "page-fragments.v2"}
    previous = checkpoint.get("split_input")
    if previous is None:
        if checkpoint.get("accepted_split") is not None:
            raise ValueError("旧片段题稿缺少完整来源输入，未自动失效。")
        return {"changed": False, "protocol": "page-fragments.v2", "reason": "no_derived_input"}
    source = before["source"]
    if (not isinstance(previous, dict) or previous.get("source_markdown") != source
            or previous.get("model_source") != source
            or previous.get("model_source_sha256") != text_sha(source)):
        raise ValueError("旧片段题稿原文、模型输入或哈希不一致，未自动失效。")
    old_input = deepcopy(previous)
    had_accepted = "accepted_split" in checkpoint
    old_accepted = deepcopy(checkpoint.get("accepted_split"))
    raw_proofs = []
    for page in raw_snapshot:
        metadata = checked_fragment_page(page)
        if metadata is None:
            raise ValueError("片段图位恢复缺少原始结构来源，未自动失效。")
        raw_proofs.append({"page_number": page["page_number"], "source_sha256": metadata["source_sha256"],
            "fragments_sha256": digest(metadata), "blocks": [{key: block[key] for key in
                ("id", "role", "number", "edge", "range", "text_sha256")} for block in metadata["blocks"]]})
    packet = {"schema": "mathbank.pdf-fragment-derivation-archive.v1", "split_input": old_input,
        "accepted_split": old_accepted, "accepted_split_present": had_accepted,
        "previous_source_sha256": text_sha(before["source"]),
        "current_source_sha256": text_sha(after["source"]), "raw_pages": raw_proofs,
        "previous_field_sources": [page["field_sources"] for page in before["source_pages"]],
        "current_field_sources": [page["field_sources"] for page in after["source_pages"]]}
    signature = digest(packet)
    archive(deepcopy(packet), signature)
    if (checkpoint.get("split_input") != old_input or ("accepted_split" in checkpoint) != had_accepted
            or checkpoint.get("accepted_split") != old_accepted or raw_pages != raw_snapshot
            or previous_layout != layout_snapshots[0] or current_layout != layout_snapshots[1]):
        raise ValueError("归档期间片段来源或旧题稿发生变化，未自动失效。")
    # Re-read claimed accepted PNG bytes immediately before retiring derived
    # cards. The original OCR and prepared pages are never modified here.
    if (proved_bundle(layout_snapshots[0])["source"] != before["source"]
            or proved_bundle(layout_snapshots[1])["source"] != after["source"]):
        raise ValueError("片段配图来源在归档期间发生变化，未自动失效。")
    checkpoint.pop("split_input", None)
    checkpoint.pop("accepted_split", None)
    return {"changed": True, "protocol": "page-fragments.v2", "archive_sha256": signature,
            "previous_source_sha256": packet["previous_source_sha256"],
            "current_source_sha256": packet["current_source_sha256"]}
