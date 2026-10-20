"""Image anchors and per-image presentation, shared by persistence and exports."""

import json
import re

IMAGE_RE = re.compile(r"!\[.*?\]\(([^)]+)\)")
ENV_RE = re.compile(r"\\(begin|end)\{(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr|choices)\}")
# Explicit sizes describe two intentionally different image roles. Keep auto
# sizing contextual: editor previews, detached groups and right-side figures
# have different readability/space constraints. These physical limits must match
# ImageLayoutTools.sizeProfilesCm in api.js (checked by executable regression).
BODY_IMAGE_SIZE_CM = {"small": (5.0, 4.2), "medium": (8.0, 6.0), "large": (10.5, 7.5)}
DETACHED_FIGURE_SIZE_CM = {"small": (5.0, 4.0), "medium": (8.0, 6.0), "large": (11.0, 8.0)}
TABLE_IMAGE_MAX_HEIGHT_CM = 4.0
COMPACT_CHOICE_IMAGE_SIZE_CM = (3.0, 3.0)
CSS_PX_PER_CM = 96.0 / 2.54
# Existing callers and persistence validation continue to use this public name.
SIZE_CM = BODY_IMAGE_SIZE_CM


def image_key(path: str) -> str:
    return re.sub(r"^/?(?:static/)?uploads/", "", str(path or "").strip())


def split_image_anchors(content: str) -> tuple[str, str]:
    """Detach only the final image cluster outside a table/choices environment."""
    source = str(content or "")
    end = len(source)
    for match in reversed(list(IMAGE_RE.finditer(source))):
        if source[match.end():end].strip():
            break
        stack = []
        for env in ENV_RE.finditer(source[:match.start()]):
            if env[1] == "begin":
                stack.append(env[2])
            elif stack and stack[-1] == env[2]:
                stack.pop()
        if stack:
            break
        end = match.start()
    return source[:end].rstrip(), source[end:].strip()


def normalize_image_layouts(value, content: str) -> dict:
    """Keep only visible image references and enum values; never accept CSS/TeX."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("插图独立排版格式无效") from exc
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError("插图独立排版必须为最多 200 项的对象")
    keys = {image_key(m[1]) for m in IMAGE_RE.finditer(content or "")}
    result = {}
    for path, layout in value.items():
        key = image_key(path)
        if not isinstance(layout, dict):
            raise ValueError("插图独立排版格式无效")
        align, size = layout.get("align", "center"), layout.get("size", "auto")
        if align not in ("left", "center", "right") or size not in ("auto", *SIZE_CM):
            raise ValueError("无效的插图位置或尺寸")
        if key in keys:
            result[key] = {"align": align, "size": size}
    return result
