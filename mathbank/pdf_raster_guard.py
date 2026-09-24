"""Bounded, local evidence for suspicious scanned-PDF illustration rectangles.

Only a clearly isolated connected line drawing with nearby short labels can
justify a correction. This is not OCR: text-heavy, ambiguous and unsupported
pages keep their original rectangles and request review. No files are written.
"""

from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path
import re
import statistics
import time

from PIL import Image, ImageFilter


MAX_SOURCE_PIXELS = 30_000_000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ANALYSIS_SIDE = 1300
MAX_RUNS = 200_000
MAX_COMPONENTS = 12_000
MAX_SECONDS = 3.0
MAX_LINE_COMPONENTS = 16
_RUN = re.compile(b"\xff+")


class _EvidenceLimit(ValueError):
    pass


def _box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
            return None
    except OverflowError:
        return None
    box = tuple(float(v) for v in value)
    return box if 0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000 else None


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _intersection(first, second):
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0, min(first[3], second[3]) - max(first[1], second[1]))


def _union(first, second):
    return (min(first[0], second[0]), min(first[1], second[1]),
            max(first[2], second[2]), max(first[3], second[3]))


def _gap(first, second):
    dx = max(0, first[0] - second[2], second[0] - first[2])
    dy = max(0, first[1] - second[3], second[1] - first[3])
    return math.hypot(dx, dy)


def _contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _components(mask, deadline):
    """Eight-connected row runs, with capped work and union-find components."""
    width, height = mask.size
    data = mask.tobytes()
    parents, boxes, counts, longest, run_boxes = [], [], [], [], []

    def find(node):
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def join(first, second):
        first, second = find(first), find(second)
        if first == second:
            return first
        if counts[first] < counts[second]:
            first, second = second, first
        parents[second] = first
        boxes[first] = _union(boxes[first], boxes[second])
        counts[first] += counts[second]
        longest[first] = max(longest[first], longest[second])
        return first

    previous = []
    for y in range(height):
        if y % 24 == 0 and time.monotonic() > deadline:
            raise _EvidenceLimit("本地图形分析达到时间上限")
        current, pointer = [], 0
        for match in _RUN.finditer(data, y * width, (y + 1) * width):
            left, right = match.start() - y * width, match.end() - y * width
            node = len(parents)
            if node >= MAX_RUNS:
                raise _EvidenceLimit("本页图像纹理过多，超过连通分析上限")
            parents.append(node)
            boxes.append((left, y, right, y + 1))
            run_boxes.append(boxes[-1])
            counts.append(right - left)
            longest.append(right - left)
            while pointer < len(previous) and previous[pointer][1] < left:
                pointer += 1
            other = pointer
            while other < len(previous) and previous[other][0] <= right:
                join(node, previous[other][2])
                other += 1
            current.append((left, right, node))
        previous = current
    result, tracked = [], {}
    for node, parent in enumerate(parents):
        if node == parent and counts[node] >= 5:
            component = {"bbox": boxes[node], "pixels": counts[node], "max_run": longest[node]}
            result.append(component)
            left, top, right, bottom = boxes[node]
            if ((right-left) / width >= 0.03 and (bottom-top) / height >= 0.03
                    and (right-left) * (bottom-top) / (width*height) >= 0.0025):
                component["runs"] = []
                tracked[node] = component
            if len(result) > MAX_COMPONENTS:
                raise _EvidenceLimit("本页小组件过多，无法可靠区分图形和文字")
    for node, box in enumerate(run_boxes):
        if node % 4096 == 0 and time.monotonic() > deadline:
            raise _EvidenceLimit("本地图形分析达到时间上限")
        component = tracked.get(find(node))
        if component is not None:
            component["runs"].append((box[0], box[1], box[2]))
    return result


def _text_rows(components, letter_height, deadline):
    rows = []
    small = [component for component in components
             if 0.45 * letter_height <= component["bbox"][3] - component["bbox"][1] <= 2.2 * letter_height]
    for index, component in enumerate(sorted(small, key=lambda item: (item["bbox"][1] + item["bbox"][3], item["bbox"][0]))):
        if index % 128 == 0 and time.monotonic() > deadline:
            raise _EvidenceLimit("本地图形分析达到时间上限")
        center = (component["bbox"][1] + component["bbox"][3]) / 2
        if rows and abs(center - rows[-1]["center"]) <= letter_height * 0.55:
            rows[-1]["items"].append(component)
        else:
            rows.append({"center": center, "items": [component]})
    prose = []
    for index, row in enumerate(rows):
        if index % 64 == 0 and time.monotonic() > deadline:
            raise _EvidenceLimit("本地图形分析达到时间上限")
        groups = []
        for component in sorted(row["items"], key=lambda item: item["bbox"][0]):
            box = component["bbox"]
            if groups and box[0] - groups[-1]["bbox"][2] <= letter_height * 2.5:
                groups[-1]["bbox"] = _union(groups[-1]["bbox"], box)
                groups[-1]["count"] += 1
            else:
                groups.append({"bbox": box, "count": 1})
        prose.extend(group["bbox"] for group in groups
                     if group["bbox"][2] - group["bbox"][0] >= letter_height * 6
                     and (group["count"] >= 4 or group["bbox"][2] - group["bbox"][0] >= letter_height * 10))
    return prose


def _is_regular_grid(mask, box):
    """A multi-row/column table is not independent line-drawing evidence."""
    def rules(image):
        width, height = image.size
        data = image.tobytes()
        levels = []
        for y in range(height):
            length = max((match.end() - match.start() for match in _RUN.finditer(data, y * width, (y + 1) * width)), default=0)
            if length >= width * 0.75 and (not levels or y - levels[-1][-1] > 2):
                levels.append([y])
            elif length >= width * 0.75:
                levels[-1].append(y)
        return len(levels)
    crop = mask.crop(box)
    return rules(crop) >= 3 and rules(crop.transpose(Image.Transpose.TRANSPOSE)) >= 3


@lru_cache(maxsize=4)
def _page_evidence(path: str, mtime_ns: int, file_size: int):
    del mtime_ns
    deadline = time.monotonic() + MAX_SECONDS
    if file_size > MAX_FILE_BYTES:
        raise _EvidenceLimit("原页图片超过本地核验大小上限")
    with Image.open(path) as image:
        if image.format != "PNG" or image.width * image.height > MAX_SOURCE_PIXELS:
            raise _EvidenceLimit("原页图片格式或像素数超出本地核验范围")
        source_size = image.size
        scale = min(1.0, MAX_ANALYSIS_SIDE / max(image.size))
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        gray = image.convert("L").resize(size, Image.Resampling.LANCZOS)
    # Small dilation reconnects thin scan strokes; it is bounded to one working
    # pixel and never used as a reason to consume nearby paragraph text.
    mask = gray.point(lambda value: 255 if value < 165 else 0).filter(ImageFilter.MaxFilter(3))
    components = _components(mask, deadline)
    if time.monotonic() > deadline:
        raise _EvidenceLimit("本地图形分析达到时间上限")
    heights = [c["bbox"][3] - c["bbox"][1] for c in components
               if 4 <= c["bbox"][3] - c["bbox"][1] <= size[1] * 0.035
               and c["bbox"][2] - c["bbox"][0] <= size[0] * 0.05]
    letter_height = float(statistics.median(heights)) if heights else 10.0
    text_rows = _text_rows(components, letter_height, deadline)
    large = []
    for component in components:
        box = component["bbox"]
        w, h = box[2] - box[0], box[3] - box[1]
        density = component["pixels"] / max(1, w * h)
        if (w / size[0] >= 0.075 and h / size[1] >= 0.065
                and 0.35 <= w / h <= 3.5 and w * h / (size[0] * size[1]) <= 0.25
                and 0.015 <= density <= 0.28 and component["max_run"] >= max(20, w * 0.25)):
            large.append(component)
    if len(large) > MAX_LINE_COMPONENTS:
        raise _EvidenceLimit("大图形候选过多，无法可靠确定独立图形")
    figures = []
    for component in large:
        box = component["bbox"]
        if any(_intersection(box, row) > 0 for row in text_rows) or _is_regular_grid(mask, box):
            continue
        radius = min(24.0, max(8.0, letter_height * 1.6))
        labels = []
        for index, other in enumerate(components):
            if index % 128 == 0 and time.monotonic() > deadline:
                raise _EvidenceLimit("本地图形分析达到时间上限")
            if other is component:
                continue
            label = other["bbox"]
            if (label[2] - label[0] <= letter_height * 3.5 and label[3] - label[1] <= letter_height * 2.0
                    and label[3] - label[1] >= 3 and other["pixels"] >= 7
                    and _gap(box, label) <= radius
                    and not _contains(box, label)
                    and not any(_intersection(label, row) > 0 for row in text_rows)):
                labels.append(label)
        if not 2 <= len(labels) <= 24:
            continue
        evidence = box
        for label in labels:
            evidence = _union(evidence, label)
        evidence = (max(0, evidence[0] - 2), max(0, evidence[1] - 2),
                    min(size[0], evidence[2] + 2), min(size[1], evidence[3] + 2))
        if any(_intersection(evidence, row) > 0 for row in text_rows):
            continue
        normalized = [evidence[0] * 1000 / size[0], evidence[1] * 1000 / size[1],
                      evidence[2] * 1000 / size[0], evidence[3] * 1000 / size[1]]
        figures.append({"bbox": tuple(normalized), "labels": len(labels),
                        "core_bbox": tuple([box[0] * 1000 / size[0], box[1] * 1000 / size[1],
                                            box[2] * 1000 / size[0], box[3] * 1000 / size[1]])})
    if time.monotonic() > deadline:
        raise _EvidenceLimit("本地图形分析达到时间上限")
    significant = tuple(
        (box[0] * 1000 / size[0], box[1] * 1000 / size[1],
         box[2] * 1000 / size[0], box[3] * 1000 / size[1])
        for component in components for box in [component["bbox"]]
        if (box[2] - box[0]) / size[0] >= 0.03 and (box[3] - box[1]) / size[1] >= 0.03
        and _area(box) / (size[0] * size[1]) >= 0.0025
    )
    return {"figures": tuple(figures), "text_rows": tuple(tuple([row[0] * 1000 / size[0], row[1] * 1000 / size[1],
             row[2] * 1000 / size[0], row[3] * 1000 / size[1]]) for row in text_rows),
            "significant_components": significant,
            "components": len(components), "large_components": len(large), "analysis_size": size,
            "source_size": source_size, "letter_height": letter_height,
            "ink_mask": mask.tobytes(),
            "significant_runs": tuple({"pixels": component["pixels"], "runs": tuple(component["runs"])}
                                      for component in components if "runs" in component)}


def _frame_ink(evidence: dict, bbox) -> int:
    """Count visible ink inside a box, independently of the line-graph detector."""
    width, height = evidence["analysis_size"]
    left, top = int(bbox[0] * width / 1000), int(bbox[1] * height / 1000)
    right, bottom = math.ceil(bbox[2] * width / 1000), math.ceil(bbox[3] * height / 1000)
    pixels = evidence["ink_mask"]
    return sum(pixels[y * width + left:y * width + right].count(255) for y in range(top, bottom))


def _cuts_component(evidence: dict, bbox) -> bool:
    """Use component pixels, not a huge page border's misleading bounding box."""
    width, height = evidence["analysis_size"]
    left, top = bbox[0] * width / 1000, bbox[1] * height / 1000
    right, bottom = bbox[2] * width / 1000, bbox[3] * height / 1000
    for component in evidence["significant_runs"]:
        inside = sum(max(0, min(x1, right) - max(x0, left))
                     for x0, y, x1 in component["runs"] if top <= y < bottom)
        outside = component["pixels"] - inside
        if inside >= 8 and outside > max(8, component["pixels"] * 0.01):
            return True
    return False


def _text_only_frame(evidence: dict, bbox) -> bool:
    """Recognize a crop dominated by paragraph ink, not a labelled diagram."""
    if any(_contains(bbox, component) for component in evidence["significant_components"]):
        return False
    total = _frame_ink(evidence, bbox)
    if total < 8:
        return False
    covered = 0
    for row in evidence["text_rows"]:
        intersection = (max(bbox[0], row[0]), max(bbox[1], row[1]),
                        min(bbox[2], row[2]), min(bbox[3], row[3]))
        if _area(intersection) > 0:
            covered += _frame_ink(evidence, intersection)
    return covered >= total * 0.90


def guard_raster_figure_bbox(image_path: str, bbox) -> dict:
    """Correct an XY/YX swap only with unique complete local graphic evidence."""
    original = _box(bbox)
    result = {"bbox": list(original) if original else bbox, "model_bbox": list(original) if original else bbox,
              "changed": False, "warnings": [], "notes": [], "method": "unchanged",
              "status": "suspect"}
    if original is None:
        result["warnings"] = ["扫描配图坐标无效，请对照原页重新框选。"]
        return result
    try:
        path = Path(image_path)
        stat = path.stat()
        evidence = _page_evidence(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except _EvidenceLimit as exc:
        result.update(status="unavailable", notes=[str(exc) + "；本地边界检查未执行，保留原视觉图框。"])
        return result
    except (OSError, ValueError, Image.DecompressionBombError):
        result["warnings"] = ["无法取得可靠的扫描图形边界证据，请对照原页核对裁剪。"]
        return result
    result.update(component_count=evidence["components"], figure_candidates=len(evidence["figures"]),
                  analysis_size=list(evidence["analysis_size"]))
    figures = evidence["figures"]
    if not figures:
        # Inapplicability is not evidence of an incorrect crop. Unlabelled
        # partitions, tables and photos can be valid illustrations. Only a
        # concrete blank crop or a visibly cut substantial component blocks it.
        if _frame_ink(evidence, original) < 8:
            result["warnings"] = ["图框内几乎没有可见内容，可能裁到了空白区域，请核对裁剪位置。"]
        elif _cuts_component(evidence, original):
            result["warnings"] = ["图框截入了未完整覆盖的图形区域，请核对是否裁掉边缘或混入邻图。"]
        elif _text_only_frame(evidence, original):
            result["warnings"] = ["图框内主要是连续正文，未见独立图形，请核对是否截到了题干或页脚。"]
        else:
            result.update(status="not_applicable", notes=[
                "本地独立线图检查不适用于该配图；保留视觉图框，未发现可据此判定的裁剪错误。"])
        return result
    swapped = (original[1], original[0], original[3], original[2])

    def coverage(frame, figure):
        return _intersection(frame, figure["bbox"]) / _area(figure["bbox"])

    def safe_frame(frame):
        return not any(_intersection(frame, row) > 0 for row in evidence["text_rows"])

    normal = [figure for figure in figures if coverage(original, figure) >= 0.80]
    target, proposal, method = None, original, "unchanged"
    if len(normal) == 1:
        target = normal[0]
        proposal = _union(original, target["bbox"])
        growth = max(abs(proposal[i] - original[i]) for i in range(4))
        if growth > 12:
            result["warnings"].append("扫描图形或标注超出原框的安全补边距离，请核对是否裁掉了图的一部分。")
        if _area(proposal) > _area(original) * 1.25:
            result["warnings"].append("完整覆盖图形需要明显扩大原框，请对照原页重新框选。")
        if not safe_frame(proposal):
            result["warnings"].append("图框与相邻正文或页脚相交，未自动扩大裁剪范围。")
        if not result["warnings"]:
            method = "bounded_padding" if proposal != original else "unchanged"
    elif len(figures) == 1 and evidence["large_components"] == 1:
        figure = figures[0]
        old_coverage, new_coverage = coverage(original, figure), coverage(swapped, figure)
        result["coverage_before"] = round(old_coverage, 4)
        result["coverage_if_swapped"] = round(new_coverage, 4)
        combined = _area(original) + _area(swapped) - _intersection(original, swapped)
        near_swap = _intersection(original, swapped) / max(1, combined) >= 0.15
        scale_x = (swapped[2] - swapped[0]) / (figure["bbox"][2] - figure["bbox"][0])
        scale_y = (swapped[3] - swapped[1]) / (figure["bbox"][3] - figure["bbox"][1])
        proposal = _union(swapped, figure["bbox"])
        if (old_coverage < 0.75 and new_coverage >= 0.95 and new_coverage - old_coverage >= 0.25
                and near_swap and 0.75 <= scale_x <= 1.5 and 0.75 <= scale_y <= 1.5
                and _area(proposal) <= _area(swapped) * 1.25 and safe_frame(proposal)):
            target, method = figure, "xy_swap"
        else:
            result["warnings"] = ["图框未完整覆盖独立线图，坐标交换或移动也缺少唯一充分证据，请对照原页核对。"]
            result["evidence_bbox"] = list(figure["bbox"])
    else:
        result["warnings"] = ["本页有多幅可能的线图，无法唯一确认当前图框，未自动移动裁剪位置。"]
    if target:
        result["evidence_bbox"] = [round(value, 4) for value in target["bbox"]]
        result["label_components"] = target["labels"]
        if any(other is not target and _intersection(proposal, other["bbox"]) > 0 for other in figures):
            result["warnings"].append("当前图框还切入另一幅独立图形，未自动确认或扩大裁剪。")
        if method == "xy_swap" and any(
            not _contains(target["bbox"], component)
            and _intersection(original, component) > 0
            and _intersection(proposal, component) < _intersection(original, component)
            for component in evidence["significant_components"]
        ):
            result["warnings"].append("原框中还有交换后会丢弃的大块墨迹，无法确认属于同一配图，未自动交换坐标。")
    if target and not result["warnings"]:
        result["bbox"] = [round(value, 4) for value in proposal]
        result["changed"] = proposal != original
        result["method"] = method
        result["status"] = "corrected" if result["changed"] else "verified"
        if method == "xy_swap":
            result["notes"].append("已由独立线图及邻近短标注证据纠正横纵坐标交换，并保留原模型框。")
        elif method == "bounded_padding":
            result["notes"].append("已按扫描线图与短标注做有限补边。")
    return result
