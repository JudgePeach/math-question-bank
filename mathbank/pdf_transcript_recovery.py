"""Reuse complete OCR pages from the same saved PDF and identical page pixels.

The first-pass checkpoint stays immutable. Recovery produces a working copy
with its own source receipt; it does not certify OCR against the original PDF.
"""
from copy import deepcopy
import hashlib
import json

from mathbank.pdf_task_resume import validate_pdf_transcript_checkpoint
from mathbank.asset_lifecycle import ASSET_LIFECYCLE_LOCK
from mathbank.question_assets import embedded_question_assets
from mathbank.task_manager import TaskCancelled

SCHEMA = "mathbank.pdf-transcript-recovery.v2"
STAGE = "pdf_transcript_recovery"


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _checked_asset(url, receipt, resolve_asset):
    if (not isinstance(receipt, dict) or set(receipt) != {"size", "sha256"}
            or type(receipt["size"]) is not int or receipt["size"] < 1):
        raise ValueError("invalid recovery asset receipt")
    path = resolve_asset(url)
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing recovery asset")
    value = path.read_bytes()
    if receipt != {"size":len(value), "sha256":hashlib.sha256(value).hexdigest()}:
        raise ValueError("changed recovery asset")


def recover_pdf_transcript_pages(store, project_id, checkpoint, page_images, page_numbers,
                                 page_texts, layout_result, *, edited_pages, resolve_asset,
                                 check_cancelled=lambda: None, extra_candidates=()):
    """Return (working texts, working layout, report), without any model calls.

    Only pages with a definite transcript-quality failure are candidates. An
    explicitly edited page always wins. History is bounded and every candidate
    must pass its sealed checkpoint and the original page-pixel SHA binding.
    """
    from mathbank.pdf_transcript_quality import transcript_quality_reasons
    texts, layout = list(page_texts), deepcopy(layout_result)
    report = {"schema":SCHEMA, "reused":False, "recovered_pages":[], "pending_pages":[],
              "original_verified":False, "ai_calls":0}
    if len(texts) != len(page_numbers) or len(page_images) != len(page_numbers):
        return texts, layout, report
    document_sha = checkpoint["document_sha256"]
    current_assets = checkpoint.get("assets") or {}
    binding = {"schema":SCHEMA, "document_sha256":document_sha, "page_numbers":page_numbers,
        "transcripts":[_sha(text) for text in texts],
        "pixels":[current_assets.get(url) for url in page_images],
        "edited_pages":{str(number):_sha(text) for number,text in sorted(edited_pages.items())}}
    fingerprint = _sha(json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    cached = store.load_stage(project_id, STAGE, fingerprint)
    if cached is not None:
        if (cached.get("binding") != binding or not isinstance(cached.get("page_texts"), list)
                or len(cached["page_texts"]) != len(texts)):
            raise ValueError("工程识图恢复缓存的输入绑定无效。")
        recovered = {row["page_number"]:row for row in cached["report"]["recovered_pages"]}
        for index, number in enumerate(page_numbers):
            text = cached["page_texts"][index]
            if number not in recovered:
                if text != texts[index]:
                    raise ValueError("工程识图恢复缓存改写了其他页面。")
                continue
            row = recovered[number]
            if (number in edited_pages or row["working_sha256"] != _sha(text)
                    or row["page_pixels"] != current_assets.get(page_images[index])
                    or transcript_quality_reasons(text)):
                raise ValueError("工程识图恢复缓存与当前原页或工作稿不一致。")
            _checked_asset(page_images[index], row["page_pixels"], resolve_asset)
        for url, receipt in cached.get("assets", {}).items():
            _checked_asset(url, receipt, resolve_asset)
        store.register_assets(project_id, list(cached.get("assets", {})))
        report = deepcopy(cached["report"])
        report["reused"] = True
        report["recovered_assets"] = sorted(cached.get("assets", {}))
        recovered_layout = deepcopy(layout_result)
        if recovered_layout is not None:
            saved_layout = cached.get("layout_result") or {}
            for index,page in enumerate(recovered_layout["pages"]):
                number = page["page_index"]+1
                if number not in recovered:
                    continue
                saved_page = next((row for row in saved_layout.get("pages", [])
                                   if row["page_index"] == number-1), None)
                if saved_page is None:
                    raise ValueError("工程识图恢复缓存缺少对应原页布局。")
                replacement = deepcopy(saved_page)
                replacement["page_image"] = page_images[page_numbers.index(number)]
                recovered_layout["pages"][index] = replacement
            recovered_layout["issues"] = [issue for issue in recovered_layout.get("issues", [])
                                          if issue.get("page_index", -1)+1 not in recovered]
            recovered_layout["issues"].extend(deepcopy([issue for issue in saved_layout.get("issues", [])
                if issue.get("page_index", -1)+1 in recovered]))
            recovered_layout["page_texts"] = list(cached["page_texts"])
        return list(cached["page_texts"]), recovered_layout, report

    pending = {number:transcript_quality_reasons(texts[index])
               for index,number in enumerate(page_numbers) if number not in edited_pages}
    pending = {number:reasons for number,reasons in pending.items() if reasons}
    recovered_assets = {}
    if pending:
        candidates = [(metadata, None) for metadata in store.find_existing(document_sha)[:8]
                      if metadata["project_id"] != project_id]
        candidates.extend(({"project_id":task.get("project_id"),
            "source_sha256":(task.get("document_input") or {}).get("sha256")}, task)
            for task in list(extra_candidates)[:8])
        for metadata, task_candidate in candidates:
            check_cancelled()
            if metadata.get("source_sha256") != document_sha:
                continue
            try:
                # A runtime task may outlive its deleted project. Its existing
                # TTL cache is still a valid source, not a restored project.
                # Hold the asset lock until the adopted figure refs are owned
                # by the current project, before runtime TTL can retire them.
                context = (store.operation(metadata["project_id"]) if task_candidate is None
                           else ASSET_LIFECYCLE_LOCK)
                with context:
                    previous = store.load_task(metadata["project_id"]) if task_candidate is None else task_candidate
                    state = previous.get("pdf_resume_state") or {}
                    prepared = state.get("prepared") or {}
                    if (state.get("document_sha256") != document_sha
                            or not isinstance(prepared.get("page_texts"), list)):
                        continue
                    # Only selected page/figure pixels are needed. An identical
                    # current page PNG can witness a deleted history preview;
                    # unrelated obsolete detail files are not a prerequisite.
                    checked = validate_pdf_transcript_checkpoint(state, document_sha)
                    old_numbers = [index+1 for index in checked["page_indices"]]
                    old_images = previous.get("page_images") or []
                    if len(old_numbers) != len(old_images) or len(prepared["page_texts"]) != len(old_numbers):
                        continue
                    for number in list(pending):
                        check_cancelled()
                        if number not in old_numbers:
                            continue
                        index, old_index = page_numbers.index(number), old_numbers.index(number)
                        pixels = current_assets.get(page_images[index])
                        if not pixels or pixels != checked["assets"].get(old_images[old_index]):
                            continue
                        text = prepared["page_texts"][old_index]
                        if not isinstance(text, str) or transcript_quality_reasons(text):
                            continue
                        _checked_asset(page_images[index], pixels, resolve_asset)
                        urls = embedded_question_assets(text)
                        receipts = {}
                        for url in urls:
                            receipt = checked["assets"].get(url)
                            _checked_asset(url, receipt, resolve_asset)
                            receipts[url] = receipt
                        old_layout = prepared.get("layout_result")
                        if layout is not None:
                            old_page = next((page for page in (old_layout or {}).get("pages", [])
                                             if page.get("page_index") == number-1), None)
                            if old_page is None:
                                continue
                            old_page = deepcopy(old_page)
                            # Navigation uses the identical current PNG. The
                            # history preview may have been explicitly deleted.
                            old_page["page_image"] = page_images[index]
                            for figure in old_page.get("figures", []):
                                url = figure.get("image_path")
                                if url:
                                    receipt = checked["assets"].get(url)
                                    _checked_asset(url, receipt, resolve_asset)
                                    receipts[url] = receipt
                            layout["pages"] = [deepcopy(old_page) if page["page_index"] == number-1 else page
                                               for page in layout["pages"]]
                            layout["issues"] = [issue for issue in layout.get("issues", [])
                                                if issue.get("page_index") != number-1]
                            layout["issues"].extend(deepcopy([issue for issue in old_layout.get("issues", [])
                                                            if issue.get("page_index") == number-1]))
                        report["recovered_pages"].append({"page_number":number,
                            "source_project_id":metadata["project_id"], "source_task_id":state["task_id"],
                            "source_kind":"project" if task_candidate is None else "retained_task",
                            "checkpoint_sha256":state["state_sha256"], "page_pixels":deepcopy(pixels),
                            "previous_sha256":_sha(texts[index]), "working_sha256":_sha(text),
                            "reason_codes":pending.pop(number), "original_verified":False})
                        texts[index] = text
                        recovered_assets.update(receipts)
                        # Register before releasing the history project's lease.
                        store.register_assets(project_id, list(receipts))
            except TaskCancelled:
                raise
            except (ValueError, OSError, KeyError, TypeError):
                report.setdefault("unavailable_candidates", 0)
                report["unavailable_candidates"] += 1
            if not pending:
                break
    report["pending_pages"] = [{"page_number":number,"reason_codes":reasons}
                                for number,reasons in pending.items()]
    report["recovered_assets"] = sorted(recovered_assets)
    if layout is not None:
        layout["page_texts"] = list(texts)
    if report["recovered_pages"]:
        store.save_stage(project_id, STAGE, {"binding":binding,"page_texts":texts,
            "layout_result":layout,"assets":recovered_assets,"report":report}, fingerprint, complete=True)
    return texts, layout, report
