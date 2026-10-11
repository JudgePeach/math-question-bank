"""Bounded raster evidence for OCR figure review, never accepted figure boxes."""

from __future__ import annotations

import math

from PIL import Image

MAX_INK_PIXELS = 2_000_000
MAX_INK_RUNS = 30_000
MAX_RELATIVE_EDGE_CORRECTION = .35
MAX_SMALL_EDGE_CORRECTION_PIXELS = 24
MAX_DIMENSION_GROWTH = 1.5


def bounded_bbox_change(original: dict, proposed: dict, dimensions: tuple[int, int]) -> bool:
    old_width, old_height = original["right"] - original["left"], original["bottom"] - original["top"]
    if (proposed["right"] - proposed["left"] > old_width * MAX_DIMENSION_GROWTH
            or proposed["bottom"] - proposed["top"] > old_height * MAX_DIMENSION_GROWTH):
        return False
    return all(abs(proposed[name] - original[name]) <= max(MAX_SMALL_EDGE_CORRECTION_PIXELS * 1000 / dimensions[axis],
               (old_width if axis == 0 else old_height) * MAX_RELATIVE_EDGE_CORRECTION)
               for name, axis in (("left", 0), ("right", 0), ("top", 1), ("bottom", 1)))


def source_pixel_bbox(bbox: dict, dimensions: tuple[int, int]) -> dict:
    width, height = dimensions
    return {"left": math.floor(bbox["left"] * width / 1000), "top": math.floor(bbox["top"] * height / 1000),
            "right": math.ceil(bbox["right"] * width / 1000), "bottom": math.ceil(bbox["bottom"] * height / 1000)}


def normalized_bbox(box: dict, dimensions: tuple[int, int]) -> dict:
    width, height = dimensions
    return {name: box[name] * 1000 / (width if name in {"left", "right"} else height)
            for name in ("left", "top", "right", "bottom")}


def context_bbox(bbox: dict, dimensions: tuple[int, int]) -> dict:
    """Context pixels may contain adjacent text; they are never delivered."""
    width, height = dimensions
    box = source_pixel_bbox(bbox, dimensions)
    dx = min(64, max(32, math.ceil((box["right"] - box["left"]) * .25)))
    dy = min(64, max(32, math.ceil((box["bottom"] - box["top"]) * .25)))
    box = {"left": max(0, box["left"] - dx), "top": max(0, box["top"] - dy),
           "right": min(width, box["right"] + dx), "bottom": min(height, box["bottom"] + dy)}
    if height <= 256 and width >= 4 * height:
        # Thin strips otherwise show the same wrong Y guess at larger scale.
        # Full short height reveals the neighboring graphic and its labels.
        box["top"], box["bottom"] = 0, height
    return box


def raster_ink_components(image: Image.Image) -> list[dict]:
    """8-neighbor run components on a bounded grayscale image, with no OCR."""
    source_size = image.size
    scale = min(1., math.sqrt(MAX_INK_PIXELS / (image.width * image.height)), 2000 / max(image.size))
    gray = image.convert("L")
    if scale < 1:
        gray = gray.resize((max(1, int(gray.width * scale)), max(1, int(gray.height * scale))), Image.Resampling.LANCZOS)
    width, height = gray.size
    data = gray.point([1 if value < 180 else 0 for value in range(256)]).tobytes()
    parents, bounds, pixels, previous = [], [], [], []
    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for y in range(height):
        offset, end = y * width, (y + 1) * width
        cursor, current, candidate = offset, [], 0
        while cursor < end:
            first = data.find(b"\x01", cursor, end)
            if first < 0: break
            last = data.find(b"\x00", first, end)
            if last < 0: last = end
            left, right = first - offset, last - offset
            index = len(parents)
            if index >= MAX_INK_RUNS: return []
            parents.append(index); bounds.append([left, y, right, y + 1]); pixels.append(right - left)
            while candidate < len(previous) and previous[candidate][1] < left: candidate += 1
            probe = candidate
            while probe < len(previous) and previous[probe][0] <= right:
                root, other = find(index), find(previous[probe][2])
                if root != other:
                    a, b = bounds[root], bounds[other]
                    parents[other] = root
                    bounds[root] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    pixels[root] += pixels[other]
                probe += 1
            current.append((left, right, index)); cursor = last
        previous = current
    candidates = []
    for index, box in enumerate(bounds):
        if find(index) != index or pixels[index] < 20 or min(box[2] - box[0], box[3] - box[1]) < 6: continue
        actual = {name: value * source_size[axis] / gray.size[axis]
                  for name, value, axis in zip(("left", "top", "right", "bottom"), box, (0, 1, 0, 1))}
        candidates.append({"id": f"ink_{len(candidates) + 1}", "kind": "raster_ink_component",
                           "bbox_pixels": actual, "bbox": normalized_bbox(actual, source_size)})
        if len(candidates) >= 500: return []
    return candidates


def compatible_ink_hint(bbox: dict, components: list[dict], dimensions: tuple[int, int]) -> list[dict]:
    """Only one compatible nearby component becomes a hint, never a crop."""
    area = (bbox["right"] - bbox["left"]) * (bbox["bottom"] - bbox["top"])
    compatible = []
    for component in components:
        box = component["bbox"]
        component_area = (box["right"] - box["left"]) * (box["bottom"] - box["top"])
        overlap = max(0, min(box["right"], bbox["right"]) - max(box["left"], bbox["left"])) * max(0, min(box["bottom"], bbox["bottom"]) - max(box["top"], bbox["top"]))
        if not .25 * area <= component_area <= 2.2 * area or overlap < .6 * component_area: continue
        if not bounded_bbox_change(bbox, box, dimensions): continue
        compatible.append(component)
    return compatible if len(compatible) == 1 else []
