"""Single-question OCR figure slots, bounded crops and faithful source binding.

The provider proposes named rectangles and unique placeholders. It never
chooses paths or source offsets. Invalid figure metadata leaves the readable
transcript and its manual-crop placeholders available to the editor.
"""

from __future__ import annotations

from collections import Counter
import io
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from PIL import Image

from mathbank.ai_json import parse_ai_json, _preserve_json_fillin_command, _repair_json_string_content
from mathbank.asset_security import normalize_raster_image, normalize_upload_asset_reference
from mathbank.content_locks import _formulas
from mathbank.math_markdown import normalize_table_math_wrappers
from mathbank.question_assets import markdown_literal_ranges


MAX_OCR_FIGURES = 20  # Same bound as the editable content_tikz_assets contract.
MAX_OCR_SOURCE_CHARS = 50_000
MAX_OCR_RESPONSE_CHARS = 200_000
MAX_CROP_SIDE = 2400
MAX_CROP_PIXELS = 4_000_000
_SLOT = re.compile(r"\[插图待补(?:\s*[:：]\s*([^\]\n]{0,80}))?\]")
_SLOT_NAME = re.compile(r"图[1-9]\d{0,2}\Z")
_LEGACY_BOX = re.compile(r"\[ILLUSTRATION_BOX:[^\]\n]*(?:\]|$)", re.I)
_MODEL_ASSET = re.compile(r"!\[[^\]]*\]\s*(?:\(|\[)|<\s*img\b|\\includegraphics\b", re.I)
_ENV = re.compile(r"\\(begin|end)\s*\{(choices|tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)\}")
_INVALID_MARKDOWN_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# JSON's n/r/t escapes overlap these ordinary TeX commands. Recognizing a
# complete command never defines a custom macro or changes its source spelling.
_JSON_CONTROL_TEX_COMMANDS = frozenset("""
nabla natural neg ne neq nu ni not notin notni nexists nless ngtr nleq ngeq
nleqslant ngeqslant nparallel nmid nshortmid nshortparallel nsubseteq nsupseteq
ntriangleleft ntriangleright ntrianglelefteq ntrianglerighteq nonumber newline
newcommand newenvironment
rho right rightarrow Rightarrow rangle rbrace rvert rVert rfloor rceil rm ref
rightleftharpoons rightharpoonup rightharpoondown rightrightarrows rightleftarrows
rightsquigarrow risingdotseq renewcommand renewenvironment rule raisebox resizebox
theta times tau tan tanh tfrac tbinom tilde text textbf textit textrm textsf texttt
textnormal textcolor textcircled textwidth textheight textperthousand textdollar
textdegree thinspace thickspace triangle triangledown triangleleft triangleright
trianglelefteq trianglerighteq therefore top to tag today tabularnewline tikz
""".split())


def _visible_slots(markdown: str) -> list[dict]:
    protected = [*markdown_literal_ranges(markdown), *[(formula.start, formula.end) for formula in _formulas(markdown)]]
    slots = []
    for match in _SLOT.finditer(markdown):
        if any(left <= match.start() < right for left, right in protected):
            continue
        label = (match.group(1) or "").strip()
        slots.append({"start": match.start(), "end": match.end(), "slot": label,
                      "placeholder": match.group(), "id": "ocr_" + uuid.uuid4().hex})
    return slots


def _checked_bbox(value) -> dict | None:
    if value is None:
        return None
    names = ("left", "top", "right", "bottom")
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("图框缺少命名坐标，请手动截图。")
    numbers = [value[name] for name in names]
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) for number in numbers):
        raise ValueError("图框坐标无效，请手动截图。")
    left, top, right, bottom = numbers
    if (not (0 <= left < right <= 1000 and 0 <= top < bottom <= 1000)
            or min(right - left, bottom - top) < 1
            or (right - left) * (bottom - top) > 800_000):
        raise ValueError("图框越界、过小或覆盖整张截图，请手动核对。")
    return dict(value)


def _recover_markdown(raw: str) -> str | None:
    """Retain a complete JSON string even if later figure metadata is broken."""
    match = re.search(r'"markdown"\s*:\s*', raw)
    if not match:
        return None
    tail = raw[match.end():]
    if not tail.startswith('"'):
        return None
    index = 1
    while index < len(tail):
        if tail[index] == "\\":
            index += 2
        elif tail[index] == '"':
            break
        else:
            index += 1
    if index >= len(tail) or (tail[index + 1:].lstrip() and tail[index + 1:].lstrip()[0] not in ",}"):
        return None
    literal = _preserve_json_fillin_command(tail[:index + 1])
    # Repair the isolated string with the established LaTeX escape policy.
    # Protect genuine JSON newline/tab/CR escapes first: the broad repair must
    # not reinterpret e.g. \nnext or \ttext as invented LaTeX commands.
    prepared, index = [], 0
    while index < len(literal):
        if literal[index] == "\\" and index + 1 < len(literal):
            pair = literal[index:index + 2]
            control = {r"\n": r"\u000a", r"\r": r"\u000d", r"\t": r"\u0009"}.get(pair)
            if control is not None:
                word = re.match(r"[A-Za-z]+", literal[index + 1:]).group()
                if word in _JSON_CONTROL_TEX_COMMANDS:
                    # Let the established multi-letter LaTeX repair escape
                    # this command, rather than decoding theta as TAB + heta.
                    prepared.append(pair)
                else:
                    prepared.append(control)
            else:
                prepared.append(pair)
            index += 2
        else:
            prepared.append(literal[index])
            index += 1
    repaired = _repair_json_string_content("".join(prepared))
    try:
        value, _end = json.JSONDecoder().raw_decode(repaired)
        return value if isinstance(value, str) and value.strip() else None
    except (ValueError, TypeError):
        return None


def parse_single_ocr_response(response) -> dict:
    """Validate independently usable text and each unique figure slot."""
    warnings = []
    structured_response = isinstance(response, dict)
    result = response if isinstance(response, dict) else None
    if result is None:
        if not isinstance(response, str) or not response.strip() or len(response) > MAX_OCR_RESPONSE_CHARS:
            raise ValueError("单题识图未返回有效内容或内容过长。")
        try:
            result = parse_ai_json(response)
            structured_response = True
        except ValueError:
            recovered = _recover_markdown(response)
            if recovered is not None:
                result = {"markdown": recovered, "figures": []}
                structured_response = True
                warnings.append("配图定位结果无法解析，已保留转录正文，请手动截图补图。")
            elif response.lstrip().startswith(("{", "```json")):
                raise ValueError("单题识图返回的JSON无效且正文不完整，请重试识别。")
            else:
                result = {"markdown": response, "figures": []}
                warnings.append("识图模型未返回配图定位，已保留转录正文，请手动截图补图。")
    if not isinstance(result, dict):
        raise ValueError("单题识图结果必须是对象。")
    markdown = result.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > MAX_OCR_SOURCE_CHARS:
        raise ValueError("单题识图的转录正文为空或过长，请缩小截图范围。")
    if structured_response and isinstance(response, str):
        recovered = _recover_markdown(response)
        if recovered is not None and recovered != markdown:
            markdown = recovered
            warnings.append("已保留公式源码，请对照原图核对公式。")
    # An otherwise valid JSON object can still consume \frac/\begin as \f/\b.
    # Recover from the original field before accepting those control bytes.
    if _INVALID_MARKDOWN_CONTROL.search(markdown):
        recovered = _recover_markdown(response) if isinstance(response, str) else None
        if recovered is None or _INVALID_MARKDOWN_CONTROL.search(recovered):
            raise ValueError("单题识图正文包含无效控制字符，请重试识别。")
        markdown = recovered
        warnings.append("已保留公式源码，请对照原图核对公式。")
    # A text table accidentally wrapped in display math still contains real
    # figure slots. Remove only its proven outer shell before classifying slots.
    markdown = normalize_table_math_wrappers(markdown)
    # Legacy tags are display placeholders, never an authorization to infer a
    # figure's source anchor or draw the entire sheet as a single illustration.
    legacy_ranges = markdown_literal_ranges(markdown)
    legacy_markers = [match for match in _LEGACY_BOX.finditer(markdown)
                      if not any(left <= match.start() < right for left, right in legacy_ranges)]
    if legacy_markers:
        for match in reversed(legacy_markers):
            markdown = markdown[:match.start()] + "[插图待补]" + markdown[match.end():]
        markdown = markdown.strip()
        warnings.append("识图模型返回了旧式图位标记，已保留补图入口，请手动核对位置。")
    literal_ranges = markdown_literal_ranges(markdown)
    if any(not any(left <= match.start() < right for left, right in literal_ranges)
           for match in _MODEL_ASSET.finditer(markdown)):
        raise ValueError("识图模型生成了未经登记的图片路径，请重试识别。")
    raw_warnings = result.get("warnings", [])
    if isinstance(raw_warnings, list):
        warnings.extend(value[:500] for value in raw_warnings[:16] if isinstance(value, str) and value.strip())
    else:
        warnings.append("识图核对提示格式无效，请对照原图检查。")
    slots = _visible_slots(markdown)
    counts = Counter(slot["slot"] for slot in slots)
    figures = result.get("figures", [])
    if not isinstance(figures, list) or len(figures) > MAX_OCR_FIGURES:
        warnings.append("配图定位清单无效或超过20幅，已保留占位，请手动截图。")
        figures = []
    figure_counts = Counter(figure.get("slot") for figure in figures
                            if isinstance(figure, dict) and isinstance(figure.get("slot"), str))
    by_slot = {figure["slot"]: figure for figure in figures
               if isinstance(figure, dict) and isinstance(figure.get("slot"), str)}
    illustrations = []
    for slot in slots[:MAX_OCR_FIGURES]:
        item = {**slot, "bbox": None, "status": "unresolved", "image_path": "",
                "tikz_code": "", "tikz_image_path": "", "reference_image_path": "", "message": "",
                "anchor_valid": bool(_SLOT_NAME.fullmatch(slot["slot"]) and counts[slot["slot"]] == 1
                                     and figure_counts[slot["slot"]] <= 1)}
        figure = by_slot.get(slot["slot"])
        if not _SLOT_NAME.fullmatch(slot["slot"]) or counts[slot["slot"]] != 1:
            item["message"] = "插图占位缺少唯一图号，请手动核对位置后截图。"
        elif figure_counts[slot["slot"]] != 1 or figure is None:
            item["message"] = "插图定位缺失或同一图号被重复绑定，请手动截图。"
        elif set(figure) - {"slot", "bbox", "review_required", "review_reason"}:
            item["message"] = "插图定位包含未经支持的字段，请手动截图。"
        else:
            try:
                item["bbox"] = _checked_bbox(figure.get("bbox"))
                review = figure.get("review_required", False)
                reason = figure.get("review_reason", "")
                if not isinstance(review, bool) or not isinstance(reason, str) or len(reason) > 500:
                    raise ValueError("插图核对说明无效，请手动截图。")
                if item["bbox"] is None or review:
                    item["message"] = reason or "插图边界尚需核对，请对照原图手动截图。"
                else:
                    item["status"] = "pending"
            except (ValueError, OverflowError) as exc:
                item["bbox"] = None
                item["message"] = str(exc)
        illustrations.append(item)
    # Duplicate or nested rectangles cannot demonstrate distinct illustrations.
    for index, first in enumerate(illustrations):
        if first["bbox"] is None:
            continue
        for second in illustrations[index + 1:]:
            if second["bbox"] is None:
                continue
            a, b = first["bbox"], second["bbox"]
            overlap = max(0, min(a["right"], b["right"]) - max(a["left"], b["left"])) * max(0, min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]))
            area = min((a["right"] - a["left"]) * (a["bottom"] - a["top"]),
                       (b["right"] - b["left"]) * (b["bottom"] - b["top"]))
            if overlap / area >= .9:
                for item in (first, second):
                    item["status"] = "unresolved"
                    item["message"] = "多幅插图的图框重复或相互包含，请手动核对后截图。"
    if len(slots) > MAX_OCR_FIGURES:
        warnings.append("插图超过20幅，剩余占位已保留，请分开截图识别。")
    if set(by_slot) - set(counts):
        warnings.append("配图清单包含未对应正文占位的图号，已拒绝自动插入这些图片。")
    warnings.extend(f"{item['slot'] or '插图'}：{item['message']}" for item in illustrations if item["message"])
    return {"markdown": markdown, "illustrations": illustrations, "warnings": list(dict.fromkeys(warnings))}


def _small_anchor(markdown: str, position: int, *, multiple: bool) -> bool:
    stack = []
    for token in _ENV.finditer(markdown, 0, position):
        if token.group(1) == "begin":
            stack.append(token.group(2))
        elif stack and stack[-1] == token.group(2):
            stack.pop()
    return multiple or bool(stack)


def prepare_ocr_figure_crop(image: Image.Image, bbox: dict) -> bytes:
    """Canonical crop bytes shared by visual review and final materialization."""
    _checked_bbox(bbox)
    width, height = image.size
    box = (math.floor(bbox["left"] * width / 1000), math.floor(bbox["top"] * height / 1000),
           math.ceil(bbox["right"] * width / 1000), math.ceil(bbox["bottom"] * height / 1000))
    if min(box[2] - box[0], box[3] - box[1]) < 4:
        raise ValueError("插图裁框过小。")
    crop = image.crop(box)
    scale = min(1, MAX_CROP_SIDE / max(crop.size), math.sqrt(MAX_CROP_PIXELS / (crop.width * crop.height)))
    if scale < 1:
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    normalized = normalize_raster_image(buffer.getvalue())
    return normalized.data


def _crop_figure(image: Image.Image, bbox: dict, *, upload_root: Path, url_prefix: str,
                 expected_sha256: str | None = None) -> tuple[Path, str]:
    data = prepare_ocr_figure_crop(image, bbox)
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("待插入裁图与已核对图片不一致。")
    path = upload_root / ("ocr_figure_" + uuid.uuid4().hex + ".png")
    created = False
    try:
        with path.open("xb") as output:
            created = True
            output.write(data)
        reference = normalize_upload_asset_reference("/" + url_prefix.strip("/") + "/" + path.name,
                                                     uploads_dir=upload_root, url_prefix=url_prefix)
        return path, reference
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def materialize_ocr_illustrations(result: dict, image: Image.Image, *, skip_tikz: bool,
                                 upload_root: str | Path, url_prefix: str, draw, compile_tikz,
                                 require_review: bool = False, verified_crops: dict | None = None) -> dict:
    """Insert each bounded source crop, optionally redrawing it with TikZ."""
    markdown = result["markdown"]
    illustrations = [dict(item) for item in result["illustrations"]]
    warnings = list(result["warnings"])
    assets, layouts, replacements = [], {}, []
    for item in illustrations:
        if item["status"] != "pending":
            continue
        instruction = f"只重绘当前参考图（{item['slot']}），保留全部线条、字母、刻度及相对位置；不要绘制其他插图或题干文字。"
        try:
            review = item.get("review")
            if require_review:
                proof = (verified_crops or {}).get(item["id"])
                if (not review or not proof or review.get("status") != "verified"
                        or any(proof.get(key) != item.get(key) for key in ("id", "slot", "bbox"))
                        or any(proof.get(key) != review.get(key) for key in ("source_sha256", "crop_sha256"))
                        or not isinstance(proof.get("crop_sha256"), str) or len(proof["crop_sha256"]) != 64):
                    if review:
                        item["review"] = {**review, "status": "unverified"}
                    raise ValueError("插图缺少本次服务端核对证据。")
            if review is not None and review.get("status") != "verified":
                raise ValueError("插图尚未完成视觉核对。")
            expected_hash = review.get("crop_sha256") if review else None
            crop_path, crop_url = _crop_figure(image, item["bbox"], upload_root=Path(upload_root),
                                              url_prefix=url_prefix, expected_sha256=expected_hash)
        except Exception:
            item.update(status="unresolved", message="自动裁图失败，已保留原位占位，请手动截图。")
            warnings.append(f"{item['slot']}：{item['message']}")
            continue
        if skip_tikz:
            item.update(status="cropped", image_path=crop_url,
                        message="已自动截取原图并放回原位，请核对图形与边缘标注是否完整。")
        else:
            try:
                code = draw(str(crop_path), markdown, instruction)
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("未获得TikZ源码。")
                rendered = compile_tikz(code)
                if not isinstance(rendered, str) or not rendered:
                    raise ValueError("未获得编译后的预览图。")
                rendered = normalize_upload_asset_reference(rendered, uploads_dir=upload_root, url_prefix=url_prefix)
            except Exception:
                item.update(status="cropped", image_path=crop_url,
                            message="TikZ重绘或编译失败，已在原位保留截图，请核对裁图范围。")
                warnings.append(f"{item['slot']}：{item['message']}")
            else:
                item.update(status="tikz", image_path=rendered, tikz_code=code,
                            tikz_image_path=rendered, reference_image_path=crop_url,
                            message="已按原位重绘，请对照原图核对线条、标注与大小。")
                assets.append({"id": item["id"], "image_path": rendered, "tikz_code": code,
                               "instruction": instruction, "reference_image_path": crop_url})
        image_url = item["image_path"]
        replacements.append((item["start"], item["end"], f"![]({image_url})"))
        if _small_anchor(markdown, item["start"], multiple=len(illustrations) > 1):
            layouts[image_url] = {"align": "left", "size": "small"}
    for start, end, replacement in sorted(replacements, reverse=True):
        markdown = markdown[:start] + replacement + markdown[end:]
    for item in illustrations:
        item.pop("start", None)
        item.pop("end", None)
    return {"markdown": markdown, "illustrations": illustrations, "tikz_assets": assets,
            "image_layouts": layouts, "warnings": list(dict.fromkeys(warnings))}
