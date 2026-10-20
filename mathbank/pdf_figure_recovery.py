"""Prepare historical page copies for verified-only figure review, without IO."""
from copy import deepcopy
import re

from .pdf_figures import describe_figure_slots
from .pdf_layout import _protected_spans
from .question_assets import image_reference_spans, markdown_literal_ranges


class PdfFigureRecoveryError(ValueError):
    """A caller may reject just this recovered page and retain its evidence."""

    def __init__(self, page_number, code):
        self.page_number = page_number
        self.code = code
        super().__init__(f"第 {page_number} 页历史配图无法唯一还原（{code}），请保留首次稿与恢复证据。")


_OPEN = re.compile(r"!\[|\\includegraphics\b|<\s*img\b", re.IGNORECASE)
_MARKDOWN = re.compile(
    r'''!\[(?:\\.|[^\]\\\n])*\]\([ \t]*(?:<(?P<angle>[^<>\n]+)>|(?P<path>[^()\s<>]+))'''
    r'''(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'))?[ \t]*\)''')


def _escaped(source, position):
    before = position - 1
    while before >= 0 and source[before] == "\\":
        before -= 1
    return (position - before - 1) % 2 == 1


def _active_markdown_images(source, page_number):
    """Return whole Markdown references; literals and math are never rewritten."""
    protected = _protected_spans(source)
    literals = markdown_literal_ranges(source, include_links=False)
    destinations = set(image_reference_spans(source, local_only=False, markdown_mode=True))
    found = []
    for opening in _OPEN.finditer(source):
        start = opening.start()
        if (_escaped(source, start) or any(a <= start < b for a, b in literals)
                or any(a < start < b for a, b in protected)):
            continue
        match = _MARKDOWN.match(source, start) if opening.group() == "![" else None
        if match is None:
            raise PdfFigureRecoveryError(page_number, "unsupported_image_reference")
        group = "angle" if match.group("angle") is not None else "path"
        path = match.group(group)
        a, b = match.span(group)
        if (a, b, path) not in destinations:
            raise PdfFigureRecoveryError(page_number, "unproved_image_reference")
        found.append({"start": start, "end": match.end(), "path": path})
    return found, protected, literals


def _paths(figure):
    return list(dict.fromkeys(value for key in ("image_path", "candidate_image_path")
                             if isinstance((value := figure.get(key)), str) and value))


def _merge_figures(page, references, page_number):
    by_id = {}
    active_paths = {reference["path"] for reference in references}
    for field in ("figures", "pending_figures"):
        rows = page.get(field) or []
        if not isinstance(rows, list):
            raise PdfFigureRecoveryError(page_number, "invalid_figure_list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise PdfFigureRecoveryError(page_number, "missing_figure_identity")
            previous = by_id.get(row["id"])
            if previous is None:
                by_id[row["id"]] = deepcopy(row)
                continue
            if (previous.get("slot_id") and row.get("slot_id")
                    and previous["slot_id"] != row["slot_id"]):
                raise PdfFigureRecoveryError(page_number, "conflicting_figure_slot")
            def rank(value):
                return (bool(active_paths.intersection(_paths(value))), bool(_paths(value)),
                        bool(value.get("image_path")))
            first, second = (row, previous) if rank(row) > rank(previous) else (previous, row)
            merged = deepcopy(first)
            for key, value in second.items():
                if key not in merged or merged[key] in (None, "", []):
                    merged[key] = deepcopy(value)
            by_id[row["id"]] = merged
    return list(by_id.values())


def _prepare_page(page, source, page_number):
    references, _protected, _literals = _active_markdown_images(source, page_number)
    figures = _merge_figures(page, references, page_number)
    owners = {}
    for figure in figures:
        for path in _paths(figure):
            owners.setdefault(path, []).append(figure["id"])
    replacements, bound = [], {}
    for reference in references:
        identifiers = owners.get(reference["path"], [])
        if len(identifiers) != 1:
            raise PdfFigureRecoveryError(page_number, "unknown_or_shared_image")
        identifier = identifiers[0]
        if identifier in bound:
            raise PdfFigureRecoveryError(page_number, "repeated_owned_image")
        figure = next(row for row in figures if row["id"] == identifier)
        existing = re.fullmatch(r"p" + str(page_number) + r"-s([1-9]\d*)", str(figure.get("slot_id", "")))
        label = "图" + existing.group(1) if existing else "恢复配图"
        replacement = "[插图待补: " + label + "]"
        replacements.append({**reference, "id": identifier, "replacement": replacement})
        bound[identifier] = reference["path"]
    text = source
    for entry in reversed(replacements):
        text = text[:entry["start"]] + entry["replacement"] + text[entry["end"]:]
    slots = describe_figure_slots(text, page_number - 1)
    active_slots = [slot for slot in slots if not any(a <= slot["start"] < b
        for a, b in [*_protected_spans(text), *markdown_literal_ranges(text, include_links=False)])]
    by_start = {slot["start"]: slot for slot in active_slots}
    by_slot = {slot["id"]: slot for slot in active_slots}
    changed_slots, delta = {}, 0
    for entry in replacements:
        slot = by_start.get(entry["start"] + delta)
        if slot is None:
            raise PdfFigureRecoveryError(page_number, "restored_slot_not_visible")
        changed_slots[entry["id"]] = slot
        delta += len(entry["replacement"]) - (entry["end"] - entry["start"])
    used = set()
    for figure in figures:
        old_slot = str(figure.get("slot_id") or "")
        slot = changed_slots.get(figure["id"]) or by_slot.get(old_slot)
        if slot is None or slot["id"] in used:
            raise PdfFigureRecoveryError(page_number, "figure_slot_not_unique")
        if re.fullmatch(r"p\d+-s\d+", old_slot) and old_slot != slot["id"]:
            raise PdfFigureRecoveryError(page_number, "conflicting_service_slot")
        used.add(slot["id"])
        paths = _paths(figure)
        if figure["id"] in bound:
            figure["image_path"] = bound[figure["id"]]
        elif paths:
            figure["image_path"] = paths[0]
        figure.update(slot_id=slot["id"], page_index=page_number-1,
                      anchor_before=text[:slot["end"]], anchor_after="", attached=False,
                      review_required=True)
        figure.pop("crop_review", None)
    replacement = deepcopy(page)
    replacement.update(figures=figures, pending_figures=[])
    return replacement, text


def prepare_recovered_figure_inputs(candidate_layout, first_pass_page_texts, working_layout,
                                    working_page_texts, page_indices, recovered_page_numbers):
    """Copy only recovered pages into candidate inputs, never certify old crops.

    All non-image characters are preserved. Unknown references or ambiguous
    ownership/slots reject just that page via ``PdfFigureRecoveryError``. Inputs
    and the original recovery/checkpoint caches are never changed.
    """
    if (not isinstance(page_indices, (list, tuple))
            or not isinstance(first_pass_page_texts, (list, tuple))
            or not isinstance(working_page_texts, (list, tuple))
            or any(type(index) is not int or index < 0 for index in page_indices)
            or len(set(page_indices)) != len(page_indices)
            or len(first_pass_page_texts) != len(page_indices)
            or len(working_page_texts) != len(page_indices)
            or any(not isinstance(value, str) for value in [*first_pass_page_texts, *working_page_texts])):
        raise ValueError("PDF recovery figure inputs do not match the selected pages")
    recovered = set(recovered_page_numbers)
    if any(type(number) is not int or number-1 not in page_indices for number in recovered):
        raise ValueError("Recovered figure page is outside the selected pages")
    result, texts = deepcopy(candidate_layout), list(first_pass_page_texts)
    if not recovered:
        return result, texts
    if not isinstance(result, dict) or not isinstance(working_layout, dict):
        raise PdfFigureRecoveryError(min(recovered), "missing_recovered_layout")
    if not isinstance(result.get("pages"), list) or not isinstance(working_layout.get("pages"), list):
        raise PdfFigureRecoveryError(min(recovered), "invalid_recovered_layout")
    if any(not isinstance(page, dict) for page in [*result["pages"], *working_layout["pages"]]):
        raise PdfFigureRecoveryError(min(recovered), "invalid_recovered_page")
    for number in sorted(recovered):
        current = [row for row in result["pages"] if row.get("page_index") == number-1]
        restored = [row for row in working_layout["pages"] if row.get("page_index") == number-1]
        if len(current) != 1 or len(restored) != 1:
            raise PdfFigureRecoveryError(number, "recovered_page_not_unique")
        local = page_indices.index(number-1)
        page, text = _prepare_page(restored[0], working_page_texts[local], number)
        result["pages"][result["pages"].index(current[0])] = page
        texts[local] = text
    if isinstance(result.get("page_texts"), list) and len(result["page_texts"]) == len(texts):
        for local, index in enumerate(page_indices):
            if index+1 in recovered:
                result["page_texts"][local] = texts[local]
    if isinstance(result.get("issues"), list) and isinstance(working_layout.get("issues"), list):
        result["issues"] = [row for row in result["issues"] if not isinstance(row, dict)
                            or row.get("page_index", -1)+1 not in recovered]
        result["issues"].extend(deepcopy([row for row in working_layout["issues"]
            if isinstance(row, dict) and row.get("page_index", -1)+1 in recovered]))
    return result, texts
