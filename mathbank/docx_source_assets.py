"""Task-local proof for small, genuinely empty leading Word raster assets."""
from dataclasses import dataclass, replace
import hashlib
from io import BytesIO
from pathlib import Path
import re

from PIL import Image


@dataclass(frozen=True)
class _EmptyRaster:
    url: str
    path: str
    normalized_sha256: str
    original_sha256: str
    descriptions: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class _WordAssetEvidence:
    source_sha256: str
    empty_rasters: tuple[_EmptyRaster, ...]


def _uniform_empty_png(data: bytes) -> bool:
    """Accept opaque white, including at most one-channel one-level noise."""
    if not data or len(data) > 2_000_000:
        return False
    try:
        with Image.open(BytesIO(data)) as image:
            if (image.format != "PNG" or getattr(image, "n_frames", 1) != 1
                    or not 0 < image.width <= 64 or not 0 < image.height <= 64
                    or getattr(image, "text", {}) or image.getexif()):
                return False
            # A profile/gamma or text payload could change the meaning of the
            # almost-white raw pixels. Only ordinary resolution metadata is
            # allowed in this small, opaque placeholder proof.
            if any(key != "dpi" for key in image.info):
                return False
            extrema = image.convert("RGBA").getextrema()
            channels = extrema[:3]
            return (extrema[3] == (255, 255)
                    and all(low >= 254 and high - low <= 1 for low, high in channels)
                    and sum(low != high for low, high in channels) <= 1)
    except (OSError, ValueError, SyntaxError):
        return False


def record_empty_raster(diagnostics, url: str, path: Path, original: bytes, normalized: bytes) -> None:
    if _uniform_empty_png(original) and _uniform_empty_png(normalized):
        diagnostics.setdefault("_empty_rasters", {})[url] = _EmptyRaster(
            url, str(path.absolute()), hashlib.sha256(normalized).hexdigest(),
            hashlib.sha256(original).hexdigest(),
        )


_AUTOMATIC_IMAGE_NAME = re.compile(r"(?:图片|图像|Picture|Image)[ \t]*[1-9]\d{0,9}", re.I)
_RESOURCE_DESCRIPTION = re.compile(
    r"[\u3400-\u9fffA-Za-z]{2,24}\((?:https?://)?"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,12}\)"
    r"(?:--|—|——)教育资源门户[，,]提供"
    r"(?:试卷|教案|课件|论文|素材)(?:、(?:试卷|教案|课件|论文|素材)){0,5}"
    r"(?:以及各类)?教学资源下载[，,]还有(?:大量而丰富的)?教学相关资讯[！!。.]?"
)


def _declared_image_description(value: str) -> bool:
    # Complete lexical consumption, not a publisher/file-name prefix whitelist.
    # Unknown descriptions can carry mathematical conditions despite white PNG.
    return bool(_AUTOMATIC_IMAGE_NAME.fullmatch(value) or _RESOURCE_DESCRIPTION.fullmatch(value))


def note_image_context(diagnostics, url: str, element) -> None:
    """Prove a plain image carrier; retain accepted non-mathematical metadata."""
    local = lambda node: node.tag.rsplit("}", 1)[-1]
    safe = local(element) in {"drawing", "pict"}
    forbidden = {"OLEObject", "object", "oMath", "oMathPara", "txbxContent", "textbox", "txBody", "chart"}
    descriptions = []
    for node in element.iter():
        tag = local(node)
        if (tag in forbidden or tag == "t" and (node.text or "").strip()
                or tag == "anchor" and node.attrib.get("behindDoc") in {"1", "true"}
                or tag in {"ln", "style", "custGeom", "solidFill", "gradFill", "pattFill", "shadow", "stroke", "fill", "sp", "cxnSp", "wsp", "grpSp", "wgp"}
                or tag in {"effectLst", "effectDag"} and len(node)
                or tag == "prstGeom" and node.attrib.get("prst") != "rect"
                or tag == "blip" and len(node)
                or tag == "graphicData" and node.attrib.get("uri") != "http://schemas.openxmlformats.org/drawingml/2006/picture"):
            safe = False
        # VML rendering depends on document-level shape definitions not owned
        # by this proof. Require an explicit no-stroke picture carrier.
        if tag == "shape" and node.attrib.get("stroked", "").lower() not in {"f", "false", "0"}:
            safe = False
        for key, value in node.attrib.items():
            attr = key.rsplit("}", 1)[-1]
            value = str(value).strip()
            if attr in {"descr", "description", "alt", "title"} and value:
                descriptions.append((tag, attr, value))
                if not _declared_image_description(value):
                    safe = False
            elif attr == "name" and tag in {"docPr", "cNvPr"} and value:
                descriptions.append((tag, attr, value))
                if not _AUTOMATIC_IMAGE_NAME.fullmatch(value):
                    safe = False
    if not safe:
        diagnostics.setdefault("_nonempty_image_contexts", set()).add(url)
    elif url in diagnostics.get("_empty_rasters", {}):
        record = diagnostics["_empty_rasters"][url]
        diagnostics["_empty_rasters"][url] = replace(record,
            descriptions=tuple(sorted(set(record.descriptions) | set(descriptions))))


def finalize_word_asset_evidence(source: str, diagnostics) -> _WordAssetEvidence:
    records = diagnostics.pop("_empty_rasters", {})
    forbidden = diagnostics.pop("_nonempty_image_contexts", set())
    return _WordAssetEvidence(hashlib.sha256(source.encode()).hexdigest(),
        tuple(record for url, record in records.items() if url not in forbidden))


def verified_empty_asset_urls(source: str, evidence, asset_paths) -> set[str]:
    if (type(evidence) is not _WordAssetEvidence
            or evidence.source_sha256 != hashlib.sha256(source.encode()).hexdigest()):
        return set()
    from .content_locks import _source_parts
    from .question_assets import markdown_literal_ranges
    parts = _source_parts(source, [], excluded_literal_ranges=markdown_literal_ranges(source))
    first_question = min((part.source_start for part in parts
                          if part.field == "content" and part.number and part.number > 0), default=0)
    urls = set()
    for record in evidence.empty_rasters:
        if record.url not in asset_paths:
            continue
        token = f"![]({record.url})"
        # A repeated source reference, or even a truly empty picture inside a
        # question/choice, is not disposable document metadata.
        if source.count(token) != 1 or not 0 <= source.find(token) < first_question:
            continue
        body = source[first_question:]
        referred = False
        for _, _, value in record.descriptions:
            if not _AUTOMATIC_IMAGE_NAME.fullmatch(value):
                continue
            number = re.search(r"\d+$", value).group()
            if re.search(r"(?:图片|图像|图|Picture|Image)[ \t]*" + number + r"(?!\d)", body, re.I):
                referred = True
                break
        if referred:
            continue
        path = Path(record.path)
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() == record.normalized_sha256:
                urls.add(record.url)
        except OSError:
            continue
    return urls
