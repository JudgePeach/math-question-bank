"""Conservative local planning of native prose and visual PDF regions.

The plan is optional: ambiguity or little useful native text returns None so
the caller keeps its whole-page path. It never treats Inspector's reconstructed
tables as a source of reading order, and never transcribes a mathematical line.
"""

from __future__ import annotations

import math
import re

from mathbank.pdf_layout import _display_rect, _normalized, _page_rect


MAX_REGIONS = 3
MAX_AREA_RATIO = 0.70
MAX_IMAGE_ONLY_AREA_RATIO = 0.65
MIN_NATIVE_CJK = 25
MAX_TEXT_LINES = 2000
MAX_CANDIDATES = 80
MAX_VISUAL_OBJECTS = 3000
REGION_MARGIN_PT = 2.0
REGION_JOIN_GAP_PT = 36.0
_PROSE_PUNCTUATION = set("，。；：？！、（）《》〈〉【】“”‘’…—,.!?;:()\u3000")


def _valid_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) for number in value):
        return None
    try:
        if any(not math.isfinite(number) or not 0 <= number <= 1000 for number in value):
            return None
    except OverflowError:
        return None
    box = tuple(float(number) for number in value)
    return box if box[0] < box[2] and box[1] < box[3] else None


def _union(first, second):
    return (min(first[0], second[0]), min(first[1], second[1]),
            max(first[2], second[2]), max(first[3], second[3]))


def _area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def _overlap(first, second):
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def _overlap_y(first, second):
    return first[1] < second[3] and second[1] < first[3]


def _cjk_count(text):
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))


def _safe_prose(text, superscript):
    if superscript or _cjk_count(text) < 6:
        return False
    for char in text:
        if char.isspace():
            if char not in " \t\r\n\u3000":
                return False
            continue
        if "\u3400" <= char <= "\u4dbf" or "\u4e00" <= char <= "\u9fff" or char in _PROSE_PUNCTUATION:
            continue
        # Digits, variables, operators, private-use glyphs and unknown symbols
        # all stay with vision. The allowlist intentionally excludes them.
        return False
    return True


def _text_rows(page, visible_rect):
    import pymupdf as fitz

    raw = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    lines = []
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans)
            if not text.strip():
                continue
            direction = line.get("dir", (1, 0))
            origin = fitz.Point(0, 0) * page.rotation_matrix
            vector = fitz.Point(*direction) * page.rotation_matrix - origin
            if vector.x < 0.9 or abs(vector.y) > 0.1:
                return None  # Vertical/sideways text needs visual reading order.
            rect = _display_rect(page, line.get("bbox"))
            if rect is None:
                return None
            lines.append({"bbox": tuple(_normalized(rect, visible_rect)), "text": text,
                          "superscript": any(int(span.get("flags", 0)) & 1 for span in spans)})
            if len(lines) > MAX_TEXT_LINES:
                return None
    # PDF extraction often puts the Chinese prefix, a sign and a superscript
    # in separate text lines. Merge vertically connected pieces before deciding
    # whether any apparent Chinese fragment is safe to retain.
    rows = []
    touch = 0.5 / visible_rect.height * 1000
    for line in sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        if rows and line["bbox"][1] <= rows[-1]["bbox"][3] + touch:
            rows[-1]["bbox"] = _union(rows[-1]["bbox"], line["bbox"])
            rows[-1]["lines"].append(line)
        else:
            rows.append({"bbox": line["bbox"], "lines": [line]})
    for row in rows:
        ordered = sorted(row.pop("lines"), key=lambda item: item["bbox"][0])
        row["text"] = " ".join(line["text"].strip() for line in ordered)
        row["safe"] = _safe_prose(row["text"], any(line["superscript"] for line in ordered))
        # Two prose columns must not be flattened into one retained sentence.
        if row["safe"] and any(
            right["bbox"][0] - left["bbox"][2] > 100
            and _cjk_count(left["text"]) >= 6 and _cjk_count(right["text"]) >= 6
            for left, right in zip(ordered, ordered[1:])
        ):
            return None
    # Staggered columns may not share a baseline. Reject independent left/right
    # prose tracks whose vertical extents overlap.
    left = [line for line in lines if _cjk_count(line["text"]) >= 8 and line["bbox"][2] < 540]
    right = [line for line in lines if _cjk_count(line["text"]) >= 8 and line["bbox"][0] > 460]
    if len(left) >= 2 and len(right) >= 2:
        left_range = (0, min(line["bbox"][1] for line in left), 1000, max(line["bbox"][3] for line in left))
        right_range = (0, min(line["bbox"][1] for line in right), 1000, max(line["bbox"][3] for line in right))
        if _overlap_y(left_range, right_range):
            return None
    return rows


def _visual_boxes(page, visible_rect):
    """Include small marks omitted by the figure-candidate size filters too."""
    import pymupdf as fitz

    boxes = []
    images = page.get_image_info()
    drawings = page.get_drawings()
    if len(images) + len(drawings) > MAX_VISUAL_OBJECTS:
        return None
    for image in images:
        rect = _display_rect(page, image["bbox"])
        if rect is not None:
            boxes.append(tuple(_normalized(rect, visible_rect)))
    for drawing in drawings:
        rect = fitz.Rect(drawing["rect"])
        # A fraction bar or detached diagram stroke can have zero-height or
        # zero-width geometry. Its visible stroke still must enter vision.
        stroke = max(0.5, float(drawing.get("width") or 0) / 2)
        rect = _display_rect(page, rect + (-stroke, -stroke, stroke, stroke))
        if rect is not None:
            boxes.append(tuple(_normalized(rect, visible_rect)))
    return boxes


def _merge_boxes(boxes, *, gap=0.0, barriers=()):
    merged = []
    for box in sorted(boxes, key=lambda value: (value[1], value[0])):
        blocked = merged and any(
            barrier[1] >= merged[-1][3] and barrier[3] <= box[1] for barrier in barriers
        )
        if merged and box[1] <= merged[-1][3] + gap and (box[1] <= merged[-1][3] or not blocked):
            merged[-1] = _union(merged[-1], box)
        else:
            merged.append(box)
    return merged


def _closed_regions(boxes, rows, candidates, width, height, visual_boxes=()):
    margin_x, margin_y = REGION_MARGIN_PT * 1000 / width, REGION_MARGIN_PT * 1000 / height
    padded = [(max(0, box[0] - margin_x), max(0, box[1] - margin_y),
               min(1000, box[2] + margin_x), min(1000, box[3] + margin_y)) for box in boxes]
    boxes = _merge_boxes(padded)
    # Closure only grows to existing bounded primitives. Every iteration must
    # consume a new boundary or merge regions, so the loop is finitely bounded.
    primitives = [row["bbox"] for row in rows] + [candidate["bbox"] for candidate in candidates] + list(visual_boxes)
    for _ in range(len(primitives) + len(boxes) + 1):
        previous = boxes
        expanded = []
        for box in boxes:
            current = box
            for primitive in primitives:
                # Regions are reading-order bands: even a line horizontally
                # outside a crop joins if its vertical span crosses that band.
                if _overlap_y(current, primitive):
                    current = _union(current, primitive)
            expanded.append(current)
        boxes = _merge_boxes(expanded)
        if boxes == previous:
            return boxes
    return None


def _region(identifier, box, candidates, page_index, width, height):
    crop_width, crop_height = box[2] - box[0], box[3] - box[1]
    localized = []
    for candidate in candidates:
        value = candidate["bbox"]
        if not _overlap(box, value):
            continue
        if not (box[0] <= value[0] and box[1] <= value[1] and value[2] <= box[2] and value[3] <= box[3]):
            return None
        localized.append({
            "id": candidate["id"], "type": candidate["type"],
            "bbox": [round((value[0] - box[0]) / crop_width * 1000, 4),
                     round((value[1] - box[1]) / crop_height * 1000, 4),
                     round((value[2] - box[0]) / crop_width * 1000, 4),
                     round((value[3] - box[1]) / crop_height * 1000, 4)],
        })
    return {"id": identifier, "bbox": list(box), "page_info": {
        "page_index": page_index, "width": width * crop_width / 1000,
        "height": height * crop_height / 1000, "candidates": localized,
    }}


def plan_pdf_regions(page, page_info: dict) -> dict | None:
    """Plan safe prose/visual bands in the visible rotated page coordinates."""
    try:
        if not isinstance(page_info, dict) or page_info.get("warnings"):
            return None
        page_index = page_info.get("page_index")
        if isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0:
            return None
        visible = _page_rect(page)
        width, height = visible.width, visible.height
        if any(not isinstance(page_info.get(key), (int, float))
               or isinstance(page_info[key], bool)
               or not math.isfinite(page_info[key])
               or abs(page_info[key] - value) > 0.01
               for key, value in (("width", width), ("height", height))):
            return None
        source_candidates = page_info.get("candidates", [])
        if not isinstance(source_candidates, list) or len(source_candidates) > MAX_CANDIDATES:
            return None
        candidates, seen = [], set()
        for candidate in source_candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str) or not candidate["id"]:
                return None
            box = _valid_box(candidate.get("bbox"))
            if box is None or candidate["id"] in seen or candidate.get("type") not in {"raster", "vector"}:
                return None
            seen.add(candidate["id"])
            candidates.append({"id": candidate["id"], "type": candidate["type"], "bbox": box})
        rows = _text_rows(page, visible)
        visual_boxes = _visual_boxes(page, visible)
        if rows is None or visual_boxes is None:
            return None
        if not rows:
            if len(candidates) != 1 or _area(candidates[0]["bbox"]) / 1_000_000 > MAX_IMAGE_ONLY_AREA_RATIO:
                return None
            boxes = _closed_regions([candidates[0]["bbox"]], [], candidates, width, height)
            if not boxes or _area(boxes[0]) / 1_000_000 > MAX_IMAGE_ONLY_AREA_RATIO:
                return None
            if any(not (boxes[0][0] <= box[0] and boxes[0][1] <= box[1]
                        and box[2] <= boxes[0][2] and box[3] <= boxes[0][3]) for box in visual_boxes):
                return None
            region = _region("r1", boxes[0], candidates, page_index, width, height)
            return {"pieces": [{"region_id": "r1"}], "regions": [region], "native_characters": 0,
                    "area_ratio": _area(boxes[0]) / 1_000_000, "kind": "image_only"} if region else None
        for row in rows:
            if any(_overlap(row["bbox"], box) for box in [candidate["bbox"] for candidate in candidates] + visual_boxes):
                row["safe"] = False
        risk = [row["bbox"] for row in rows if not row["safe"]] + [candidate["bbox"] for candidate in candidates] + visual_boxes
        if not risk:
            return None
        barriers = [row["bbox"] for row in rows if row["safe"]]
        # Keep large blank gaps out of the visual payload where the region
        # budget permits; otherwise combine whole risky bands between retained
        # prose rows. Both alternatives use the same full-primitive closure.
        grouped = _merge_boxes(risk, gap=REGION_JOIN_GAP_PT * 1000 / height, barriers=barriers)
        boxes = _closed_regions(grouped, rows, candidates, width, height, visual_boxes)
        if boxes is not None and len(boxes) > MAX_REGIONS:
            grouped = _merge_boxes(risk, gap=1000, barriers=barriers)
            boxes = _closed_regions(grouped, rows, candidates, width, height, visual_boxes)
        if not boxes or len(boxes) > MAX_REGIONS:
            return None
        area_ratio = sum(_area(box) for box in boxes) / 1_000_000
        if area_ratio > MAX_AREA_RATIO:
            return None
        retained = [row for row in rows if not any(_overlap_y(row["bbox"], box) for box in boxes)]
        if any(not row["safe"] for row in retained):
            return None
        native_cjk = sum(_cjk_count(row["text"]) for row in retained)
        if native_cjk < MIN_NATIVE_CJK:
            return None
        regions = [_region(f"r{index + 1}", box, candidates, page_index, width, height)
                   for index, box in enumerate(boxes)]
        if any(region is None for region in regions):
            return None
        events = [(row["bbox"][1], {"text": row["text"]}) for row in retained]
        events.extend((region["bbox"][1], {"region_id": region["id"]}) for region in regions)
        pieces = []
        for _, piece in sorted(events, key=lambda item: item[0]):
            if "text" in piece and pieces and "text" in pieces[-1]:
                pieces[-1]["text"] += "\n" + piece["text"]
            else:
                pieces.append(piece)
        return {"pieces": pieces, "regions": regions,
                "native_characters": sum(len(re.sub(r"\s", "", piece["text"])) for piece in pieces if "text" in piece),
                "area_ratio": area_ratio, "kind": "mixed"}
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RuntimeError):
        # Optional planning must never replace a usable whole-page fallback.
        return None
