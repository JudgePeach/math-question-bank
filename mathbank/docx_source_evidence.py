"""Bounded native DOCX page evidence, never a rendering of extracted Markdown.

LibreOffice normally reads an untouched private copy in an isolated profile.
A separately signed body-only path can detach a tiny footer picture's external
link while retaining its embedded cache and every layout byte. Only bounded
page PNGs survive conversion. No model or external resource reader is invoked.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable
import uuid
import zipfile
from xml.parsers import expat
from xml.sax.saxutils import escape

from defusedxml import ElementTree as SafeET

from mathbank.docx_helper import MAX_DOCUMENT_XML, _validate_archive
from mathbank.paths import SYSTEM_GENERATED_DIR
from mathbank.task_manager import TaskCancelled


MAX_DOCX_BYTES = 20 * 1024 * 1024
MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_PAGES = 40
MAX_PAGE_PIXELS = 8_000_000
MAX_TOTAL_PIXELS = 100_000_000
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
MAX_PAGE_TEXT = 50000
MAX_TOTAL_TEXT = 500000
RENDER_DPI = 150
RENDER_TIMEOUT_SECONDS = 90
EVIDENCE_KIND = "original_docx_render"
EVIDENCE_VERSION = 2
FOOTER_CACHE_EVIDENCE_KIND = "docx_embedded_footer_cache_render"
FOOTER_CACHE_EVIDENCE_VERSION = 1
_EVIDENCE_SIGNING_KEY = os.urandom(32)
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_VML_NS = "urn:schemas-microsoft-com:vml"
_OFFICE_NS = "urn:schemas-microsoft-com:office:office"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TASK_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_DYNAMIC_FIELDS = re.compile(r"\b(?:DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|DATABASE)\b", re.IGNORECASE)


class _EvidenceError(ValueError):
    """Only fixed local messages may be returned in ordinary task notes."""


class _ExternalResourceError(_EvidenceError):
    pass


def _no_cancel() -> None:
    pass


def find_docx_renderer(renderer_path: str | None = None) -> str | None:
    """Use an explicitly configured or already installed native renderer."""
    configured = renderer_path or os.getenv("MATHBANK_SOFFICE_PATH")
    if configured:
        candidate = Path(configured).expanduser()
        return str(candidate.resolve()) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    # Codex can provide an existing headless runtime even when no desktop
    # LibreOffice is installed. This is optional, never a package dependency.
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice"
    candidates = [bundled]
    for name in ("soffice", "libreoffice", "soffice.exe"):
        path = shutil.which(name)
        if path:
            candidates.append(Path(path))
    if sys.platform == "darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    if os.name == "nt":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / "LibreOffice/program/soffice.exe")
    return next((str(path.resolve()) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def _validate_source(data: bytes) -> None:
    if not isinstance(data, bytes) or not data or len(data) > MAX_DOCX_BYTES:
        raise _EvidenceError("原Word文件为空或超过原生渲染大小上限")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            _validate_archive(archive)
            names = archive.namelist()
            if "word/document.xml" not in names or len(set(names)) != len(names):
                raise _EvidenceError("原Word文档主体缺失或压缩包存在重复条目")
            if any("vbaproject" in name.lower() for name in names):
                raise _EvidenceError("原Word包含宏内容，当前只读渲染不处理此类文件")
            for name in names:
                if not (name.endswith(".rels") or name.startswith("word/") and name.endswith(".xml")):
                    continue
                if archive.getinfo(name).file_size > MAX_DOCUMENT_XML:
                    raise _EvidenceError("原Word的XML资料超过渲染检查上限")
                root = SafeET.fromstring(archive.read(name))
                if name.endswith(".rels"):
                    for relation in root:
                        if relation.attrib.get("TargetMode", "").lower() == "external" and not relation.attrib.get("Type", "").endswith("/hyperlink"):
                            raise _ExternalResourceError("原Word含需读取外部资源的链接，无法生成独立只读视觉证据")
                else:
                    instructions = [element.text or "" for element in root.iter() if element.tag.endswith("}instrText")]
                    instructions.extend(value for element in root.iter() if element.tag.endswith("}fldSimple")
                                        for key, value in element.attrib.items() if key.endswith("}instr"))
                    if _DYNAMIC_FIELDS.search(" ".join(instructions)):
                        raise _EvidenceError("原Word包含外部动态字段，当前只读渲染不更新此类内容")
    except _EvidenceError:
        raise
    except Exception as exc:
        raise _EvidenceError("原Word文件格式或XML资料无法安全读取") from exc


def _xml_nodes(data: bytes) -> list[dict]:
    """Read semantic XML and exact byte spans without reserializing namespaces."""
    # This narrow derivative supports UTF-8 XML only; ordinary rendering is
    # unaffected. DTD/entity input cannot reach a byte-editing fallback.
    data.decode("utf-8")
    declared_encoding = re.search(rb"<\?xml\b[^?]*\bencoding\s*=\s*['\"]([^'\"]+)['\"]", data[:256], re.IGNORECASE)
    if declared_encoding and declared_encoding.group(1).lower() not in {b"utf-8", b"utf8"}:
        raise _EvidenceError("页脚缓存图的XML编码不属于受限UTF-8格式")
    parser = expat.ParserCreate(namespace_separator="}")
    parser.namespace_prefixes = True
    nodes, stack = [], []

    def tag_end(start):
        quote = None
        for index in range(start, len(data)):
            value = data[index]
            if quote is not None:
                if value == quote:
                    quote = None
            elif value in (34, 39):
                quote = value
            elif value == 62:
                return index + 1
        raise _EvidenceError("页脚缓存图的XML边界无法安全确认")

    def start(name, attrs):
        if len(stack) >= 256 or len(nodes) >= 200000:
            raise _EvidenceError("页脚缓存图的XML结构超过检查上限")
        begin = parser.CurrentByteIndex
        node = {"name": "}".join(name.split("}")[:2]), "attrs": {}, "attr_names": {},
                "start": begin, "start_end": tag_end(begin), "parent": stack[-1] if stack else None}
        for key, value in attrs.items():
            parts = key.split("}")
            expanded = "}".join(parts[:2])
            node["attrs"][expanded] = value
            node["attr_names"][expanded] = parts[2] + ":" + parts[1] if len(parts) == 3 else parts[-1]
        nodes.append(node)
        stack.append(len(nodes) - 1)

    def end(_name):
        node = nodes[stack.pop()]
        node["end"] = node["start_end"] if data[node["start_end"] - 2:node["start_end"]] == b"/>" else tag_end(parser.CurrentByteIndex)

    def reject(*_args):
        raise _EvidenceError("页脚缓存图含不支持的XML声明")
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.Parse(data, True)
    return nodes


def _part_target(owner: str, target: str) -> str:
    if not target or "\\" in target or ":" in target or "#" in target or "?" in target or target.startswith("/"):
        raise _EvidenceError("页脚缓存图的内嵌资源路径不明确")
    name = posixpath.normpath(posixpath.join(posixpath.dirname(owner), target))
    if not name.startswith("word/") or name.startswith("../"):
        raise _EvidenceError("页脚缓存图的内嵌资源路径不明确")
    return name


def _footer_cache_rendering_copy(data: bytes) -> tuple[bytes, dict]:
    """Detach only paired, fixed tiny footer caches; every other hazard blocks."""
    try:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_DOCX_BYTES:
            raise _EvidenceError("原Word文件为空或超过原生渲染大小上限")
        with zipfile.ZipFile(BytesIO(data)) as archive:
            _validate_archive(archive)
            names = archive.namelist()
            if len(set(names)) != len(names) or "word/document.xml" not in names:
                raise _EvidenceError("页脚缓存图的原Word主体不完整")
            contents = {name: archive.read(name) for name in names}
            changes, resources = {}, []
            document_nodes = _xml_nodes(contents["word/document.xml"])
            document_rels = _xml_nodes(contents.get("word/_rels/document.xml.rels", b"<Relationships/>"))
            footer_ids = {n["attrs"].get(_REL_NS + "}id") for n in document_nodes
                          if n["name"] == _WORD_NS + "}footerReference"}
            linked_footers = {_part_target("word/document.xml", n["attrs"].get("Target", ""))
                             for n in document_rels if n["name"] == _PACKAGE_REL_NS + "}Relationship"
                             and n["attrs"].get("Id") in footer_ids
                             and n["attrs"].get("Type") == _REL_NS + "/footer"
                             and not n["attrs"].get("TargetMode")}
            for rel_part in names:
                if not rel_part.endswith(".rels"):
                    continue
                rel_nodes = _xml_nodes(contents[rel_part])
                relations = [n for n in rel_nodes if n["name"] == _PACKAGE_REL_NS + "}Relationship"]
                external = [n for n in relations if n["attrs"].get("TargetMode", "").lower() == "external"
                            and not n["attrs"].get("Type", "").endswith("/hyperlink")]
                if not external:
                    continue
                match = re.fullmatch(r"word/_rels/(footer[0-9]+\.xml)\.rels", rel_part)
                if not match or "word/" + match.group(1) not in linked_footers:
                    raise _EvidenceError("原Word外部资源并非可隔离的已缓存页脚图")
                footer = "word/" + match.group(1)
                if footer not in contents:
                    raise _EvidenceError("原Word页脚缓存图的主体缺失")
                footer_nodes = _xml_nodes(contents[footer])
                if not footer_nodes or footer_nodes[0]["name"] != _WORD_NS + "}ftr":
                    raise _EvidenceError("页脚缓存图不属于明确的页脚XML")
                ids = [n["attrs"].get("Id") for n in relations]
                if any(not value for value in ids) or len(set(ids)) != len(ids):
                    raise _EvidenceError("页脚缓存图的关系标识不唯一")
                edits, removed = [], []
                for relation in external:
                    if len(resources) >= 16:
                        raise _EvidenceError("页脚外链缓存图超过有界检查上限")
                    attrs = relation["attrs"]
                    if (attrs.get("Type") != _REL_NS + "/image" or not attrs.get("Target", "").lower().startswith("file:///")
                            or not attrs.get("Target", "").lower().endswith(".png")):
                        raise _EvidenceError("页脚外部资源不属于受限缓存PNG图")
                    uses = [n for n in footer_nodes if attrs["Id"] in n["attrs"].values()]
                    if (len(uses) != 1 or uses[0]["name"] != _VML_NS + "}imagedata"
                            or uses[0]["attrs"].get(_REL_NS + "}href") != attrs["Id"]):
                        raise _EvidenceError("页脚缓存图外链存在未知或重复用途")
                    image = uses[0]
                    if (image["parent"] is None or any(key not in {_REL_NS + "}id", _REL_NS + "}href", _OFFICE_NS + "}title"}
                                                        for key in image["attrs"])):
                        raise _EvidenceError("页脚缓存图节点存在不明确的附加属性")
                    shape = footer_nodes[image["parent"]]
                    if (shape["parent"] is None or footer_nodes[shape["parent"]]["name"] != _WORD_NS + "}pict"):
                        raise _EvidenceError("页脚缓存图的载体不属于受限页脚图片")
                    style_pairs = [piece.strip().split(":", 1) for piece in shape["attrs"].get("style", "").split(";") if piece.strip()]
                    if any(len(pair) != 2 for pair in style_pairs) or len({p[0].strip() for p in style_pairs}) != len(style_pairs):
                        raise _EvidenceError("页脚缓存图的固定布局不明确")
                    style = {key.strip(): value.strip() for key, value in style_pairs}
                    allowed_styles = {"position", "left", "top", "margin-left", "margin-top", "height", "width", "z-index",
                                      "mso-width-relative", "mso-height-relative"}
                    if shape["name"] != _VML_NS + "}shape" or style.get("position") != "absolute" or set(style) - allowed_styles:
                        raise _EvidenceError("页脚缓存图布局可能影响正文，未生成正文证据")
                    for dimension in ("width", "height"):
                        if not re.fullmatch(r"(?:0\.[0-9]+|[0-9]+(?:\.[0-9]+)?)pt", style.get(dimension, "")) or not 0 < float(style[dimension][:-2]) <= 0.1:
                            raise _EvidenceError("页脚缓存图并非受限微小固定尺寸，未生成正文证据")
                    children = [n for n in footer_nodes if n["parent"] == image["parent"]]
                    allowed_children = {_VML_NS + "}" + key for key in ("path", "fill", "stroke", "imagedata")} | {_OFFICE_NS + "}lock"}
                    if (sum(n["name"] == _VML_NS + "}imagedata" for n in children) != 1
                            or any(n["name"] not in allowed_children for n in children)
                            or any(n["parent"] is not None and footer_nodes[n["parent"]] in children for n in footer_nodes)):
                        raise _EvidenceError("页脚缓存图包含未确认的布局子节点")
                    cached = next((n for n in relations if n["attrs"].get("Id") == image["attrs"].get(_REL_NS + "}id")), None)
                    if (cached is None or cached["attrs"].get("Type") != _REL_NS + "/image" or cached["attrs"].get("TargetMode")):
                        raise _EvidenceError("页脚外链没有唯一内嵌缓存图")
                    cached_part = _part_target(footer, cached["attrs"].get("Target", ""))
                    if not re.fullmatch(r"word/media/[^/]+\.png", cached_part, re.IGNORECASE) or cached_part not in contents:
                        raise _EvidenceError("页脚内嵌PNG缓存缺失")
                    from PIL import Image
                    png = contents[cached_part]
                    if not 0 < len(png) <= MAX_PAGE_BYTES:
                        raise _EvidenceError("页脚内嵌PNG缓存超过检查上限")
                    with Image.open(BytesIO(png)) as picture:
                        if picture.format != "PNG" or getattr(picture, "n_frames", 1) != 1 or not 0 < picture.width * picture.height <= MAX_PAGE_PIXELS:
                            raise _EvidenceError("页脚内嵌缓存不是有界单帧PNG")
                        png_size = [picture.width, picture.height]
                        picture.verify()
                    tag = contents[footer][image["start"]:image["start_end"]]
                    lexical = re.escape(image["attr_names"][_REL_NS + "}href"].encode("utf-8"))
                    attribute = re.compile(rb"\s+" + lexical + rb"\s*=\s*(?:\"[^\"]*\"|'[^']*')")
                    matches = list(attribute.finditer(tag))
                    if len(matches) != 1:
                        raise _EvidenceError("页脚外链属性无法按字节安全隔离")
                    item = matches[0]
                    edits.append((image["start"] + item.start(), image["start"] + item.end()))
                    removed.append((relation["start"], relation["end"]))
                    resources.append({"footer_part": footer, "relationship_part": rel_part, "external_relationship_id": attrs["Id"],
                                      "cached_relationship_id": cached["attrs"]["Id"], "cached_image_part": cached_part,
                                      "cached_image_sha256": hashlib.sha256(png).hexdigest(), "cached_image_pixels": png_size,
                                      "shape_layout_sha256": hashlib.sha256(contents[footer][shape["start"]:shape["end"]].replace(tag, attribute.sub(b"", tag, count=1), 1)).hexdigest(),
                                      "operation": "detach_external_href_keep_embedded_cache_and_layout"})
                for part, ranges in ((footer, edits), (rel_part, removed)):
                    changed = contents[part]
                    for start, end in sorted(ranges, reverse=True):
                        changed = changed[:start] + changed[end:]
                    changes[part] = changed
            if not resources or len(resources) > 16:
                raise _EvidenceError("原Word不符合受限页脚缓存图隔离规则")
            output = BytesIO()
            with zipfile.ZipFile(output, "w") as derived:
                for info in archive.infolist():
                    derived.writestr(info, changes.get(info.filename, contents[info.filename]))
                derived.comment = archive.comment
            rendering_copy = output.getvalue()
            _validate_source(rendering_copy)  # Macros, dynamics and all other external resources still block.
            unchanged = {name: hashlib.sha256(value).hexdigest() for name, value in contents.items() if name not in changes}
            receipt = {"receipt_version": 1, "operation": "footer_cached_image_external_link_detachment", "body_parts_unchanged": True,
                       "original_sha256": hashlib.sha256(data).hexdigest(), "derivative_sha256": hashlib.sha256(rendering_copy).hexdigest(),
                       "unchanged_parts_sha256": unchanged,
                       "changed_parts": {name: {"original_sha256": hashlib.sha256(contents[name]).hexdigest(),
                                                "derivative_sha256": hashlib.sha256(value).hexdigest()} for name, value in changes.items()},
                       "footer_resources": resources}
            return rendering_copy, receipt
    except _EvidenceError:
        raise
    except Exception as exc:
        raise _EvidenceError("页脚缓存图无法按受限规则安全隔离") from exc


def _stop_own_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T"], capture_output=True, timeout=3)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=3)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=3)


def _configure_native_fonts(work: Path, environment: dict) -> None:
    """Expose already-installed macOS CJK fonts to isolated headless builds."""
    if sys.platform != "darwin":
        return
    roots = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    asset_root = Path("/System/Library/AssetsV2")
    if asset_root.is_dir():
        roots.extend(sorted(asset_root.glob("com_apple_MobileAsset_Font*")))
    tex_root = Path("/usr/local/texlive")
    if tex_root.is_dir():
        roots.extend(sorted(tex_root.glob("*/texmf-dist/fonts/opentype/public/fandol")))
    roots = [root for root in roots if root.is_dir()]
    if not roots:
        return
    font_cache = work / "font-cache"
    font_cache.mkdir()
    aliases = []
    if Path("/System/Library/Fonts/Supplemental/Songti.ttc").is_file():
        aliases = [{"from": name, "to": "Songti SC"} for name in ("宋体", "SimSun")]
    config = ('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig>'
              + "".join("<dir>" + escape(str(root)) + "</dir>" for root in roots)
              + "<cachedir>" + escape(str(font_cache)) + "</cachedir>"
              + "".join("<alias><family>" + escape(item["from"]) + "</family><prefer><family>"
                        + escape(item["to"]) + "</family></prefer></alias>" for item in aliases)
              + "</fontconfig>")
    font_config = work / "fonts.conf"
    font_config.write_text(config, encoding="utf-8")
    environment["FONTCONFIG_FILE"] = str(font_config)
    (work / "font-rendering.json").write_text(json.dumps({"font_configuration": "isolated_existing_macos_fonts",
                                                        "configured_font_fallbacks": aliases}, ensure_ascii=False), encoding="utf-8")


def _font_visibility(page) -> dict:
    """A ToUnicode string is not evidence that a CJK glyph was actually drawn."""
    count = 0
    fonts = set()
    for span in page.get_texttrace():
        if span.get("type") == 3 or span.get("opacity", 1) <= 0:
            continue  # Deliberately invisible source text is not page content.
        for character in span.get("chars", []):
            code, _, _, bbox = character
            if 0x3400 <= code <= 0x9FFF or 0x20000 <= code <= 0x323AF:
                count += 1
                fonts.add(span.get("font", ""))
                if min(bbox[2] - bbox[0], bbox[3] - bbox[1]) <= 0.05:
                    raise _EvidenceError("原Word渲染出现不可见中文字符，无法作为原文视觉核验依据")
    return {"cjk_characters": count, "zero_width_cjk": 0, "cjk_fonts": sorted(fonts)}


def _native_pdf(renderer: str, docx_path: Path, work: Path, check_cancelled: Callable[[], None]) -> Path:
    profile = work / "profile"
    (profile / "user").mkdir(parents=True)
    # No user profile is touched. Embedded macros and linked-document updates
    # remain disabled even if the desktop app has more permissive preferences.
    (profile / "user/registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
        '<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>'
        '</oor:items>', encoding="utf-8",
    )
    destination = work / "converted"
    destination.mkdir()
    command = [renderer, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless", "--nologo", "--nodefault",
               "--norestore", "--nolockcheck", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(destination), str(docx_path)]
    environment = dict(os.environ)
    if sys.platform == "darwin" and Path("/private/tmp").is_dir():
        environment["TMPDIR"] = "/private/tmp"
    _configure_native_fonts(work, environment)
    kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    check_cancelled()
    with (work / "conversion.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        started = time.monotonic()
        try:
            while process.poll() is None:
                check_cancelled()
                if time.monotonic() - started > RENDER_TIMEOUT_SECONDS:
                    raise _EvidenceError("原Word原生渲染超过90秒上限")
                if log.tell() > 2 * 1024 * 1024:
                    raise _EvidenceError("原Word原生渲染日志异常，已停止转换")
                time.sleep(0.1)
            check_cancelled()
            if process.returncode != 0:
                raise _EvidenceError(f"原Word原生渲染未成功结束（退出码{process.returncode}）")
        finally:
            _stop_own_process(process)
    pdf = destination / "original.pdf"
    if not pdf.is_file() or not 0 < pdf.stat().st_size <= MAX_PDF_BYTES:
        raise _EvidenceError("原Word原生渲染未生成有效且有界的PDF")
    return pdf


def _manifest_hash(pages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(pages, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sign_body_render_evidence(evidence: dict) -> None:
    payload = {key: value for key, value in evidence.items() if key != "evidence_signature"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    evidence["evidence_signature"] = hmac.new(_EVIDENCE_SIGNING_KEY, encoded, hashlib.sha256).hexdigest()


def validate_docx_body_render_evidence(evidence: dict) -> bool:
    """Accept ordinary v2 evidence, or this process's signed body-only copy.

    The caller still resolves every selected PNG and checks its signed byte
    digest before and after model work. No filesystem path or external target
    is accepted by this JSON-only contract validator. A process restart makes
    an old derivative signature unusable and requires fresh local rendering.
    """
    if not isinstance(evidence, dict) or evidence.get("status") != "ready":
        return False
    if evidence.get("evidence_kind") == EVIDENCE_KIND and evidence.get("evidence_version") == EVIDENCE_VERSION:
        # A derivative report cannot shed its kind while retaining its receipt.
        # Ordinary v2 has never emitted any of these derivative-only fields.
        return not ({"evidence_signature", "footer_cache_receipt", "derivative_sha256", "rendering_copy_sha256",
                     "evidence_scope", "body_original_same", "original_sha256"} & evidence.keys())
    try:
        if (evidence.get("evidence_kind") != FOOTER_CACHE_EVIDENCE_KIND
                or type(evidence.get("evidence_version")) is not int or evidence["evidence_version"] != FOOTER_CACHE_EVIDENCE_VERSION
                or evidence.get("evidence_scope") != "body_only" or evidence.get("body_original_same") is not True
                or not isinstance(evidence.get("task_id"), str) or not _TASK_TOKEN.fullmatch(evidence["task_id"])):
            return False
        signature = evidence.get("evidence_signature")
        if not isinstance(signature, str) or not _SHA256.fullmatch(signature):
            return False
        payload = {key: value for key, value in evidence.items() if key != "evidence_signature"}
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        expected = hmac.new(_EVIDENCE_SIGNING_KEY, encoded, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        receipt = evidence.get("footer_cache_receipt")
        original, derivative = evidence.get("original_sha256"), evidence.get("derivative_sha256")
        if (not isinstance(original, str) or not _SHA256.fullmatch(original)
                or not isinstance(derivative, str) or not _SHA256.fullmatch(derivative) or original == derivative
                or original != evidence.get("source_sha256") or derivative != evidence.get("rendering_copy_sha256")
                or not isinstance(receipt, dict) or receipt.get("receipt_version") != 1
                or receipt.get("operation") != "footer_cached_image_external_link_detachment"
                or receipt.get("body_parts_unchanged") is not True
                or receipt.get("original_sha256") != original or receipt.get("derivative_sha256") != derivative):
            return False
        unchanged, changed, resources = receipt.get("unchanged_parts_sha256"), receipt.get("changed_parts"), receipt.get("footer_resources")
        if (not isinstance(unchanged, dict) or not {"word/document.xml", "[Content_Types].xml"} <= unchanged.keys()
                or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in unchanged.values())
                or not isinstance(changed, dict) or not changed or set(unchanged) & set(changed)
                or not isinstance(resources, list) or not 1 <= len(resources) <= 16):
            return False
        expected_parts = set()
        for resource in resources:
            footer, relation = resource.get("footer_part"), resource.get("relationship_part")
            if (not isinstance(footer, str) or not re.fullmatch(r"word/footer[0-9]+\.xml", footer)
                    or relation != "word/_rels/" + PurePosixPath(footer).name + ".rels"
                    or resource.get("operation") != "detach_external_href_keep_embedded_cache_and_layout"
                    or resource.get("cached_image_sha256") != unchanged.get(resource.get("cached_image_part"))
                    or not _SHA256.fullmatch(resource.get("shape_layout_sha256", ""))):
                return False
            expected_parts.update((footer, relation))
        if set(changed) != expected_parts:
            return False
        for pair in changed.values():
            if (not isinstance(pair, dict) or set(pair) != {"original_sha256", "derivative_sha256"}
                    or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in pair.values())
                    or pair["original_sha256"] == pair["derivative_sha256"]):
                return False
        pages = evidence.get("pages")
        if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES
                or evidence.get("manifest_sha256") != _manifest_hash(pages)
                or evidence.get("page_numbers") != list(range(1, len(pages) + 1))
                or evidence.get("page_images") != [page.get("image_path") for page in pages]):
            return False
        for number, page in enumerate(pages, 1):
            if (type(page.get("page_number")) is not int or page["page_number"] != number
                    or not isinstance(page.get("text"), str) or len(page["text"]) > MAX_PAGE_TEXT
                    or not isinstance(page.get("image_path"), str) or not _SHA256.fullmatch(page.get("image_sha256", ""))):
                return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return False


def _reuse_cached(cached: dict | None, source_hash: str, task_id: str, output_dir: Path, url_prefix: str, *,
                  expected_kind: str = EVIDENCE_KIND, expected_derivative_sha256: str | None = None) -> dict | None:
    if (not isinstance(cached, dict) or cached.get("status") != "ready" or cached.get("source_sha256") != source_hash
            or cached.get("task_id") != task_id or cached.get("evidence_kind") != expected_kind
            or not validate_docx_body_render_evidence(cached)
            or (expected_kind == FOOTER_CACHE_EVIDENCE_KIND and cached.get("derivative_sha256") != expected_derivative_sha256)):
        return None
    pages = cached.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES or cached.get("manifest_sha256") != _manifest_hash(pages):
        return None
    if cached.get("page_numbers") != list(range(1, len(pages) + 1)) or cached.get("page_images") != [page.get("image_path") for page in pages]:
        return None
    total_bytes = total_pixels = total_text = 0
    for number, page in enumerate(pages, 1):
        if (not isinstance(page, dict) or type(page.get("page_number")) is not int or page["page_number"] != number
                or not isinstance(page.get("image_path"), str) or not isinstance(page.get("text"), str)
                or len(page["text"]) > MAX_PAGE_TEXT):
            return None
        if any(isinstance(page.get(key), bool) or not isinstance(page.get(key), (int, float))
               or not math.isfinite(page[key]) or page[key] <= 0 for key in ("width", "height")):
            return None
        if any(type(page.get(key)) is not int or page[key] <= 0 for key in ("pixel_width", "pixel_height")):
            return None
        pixels = page["pixel_width"] * page["pixel_height"]
        total_pixels += pixels
        total_text += len(page["text"])
        font_check = page.get("font_check")
        if (pixels > MAX_PAGE_PIXELS or total_pixels > MAX_TOTAL_PIXELS or total_text > MAX_TOTAL_TEXT
                or not isinstance(font_check, dict) or font_check.get("zero_width_cjk") != 0
                or type(font_check.get("cjk_characters")) is not int or font_check["cjk_characters"] < 0):
            return None
        name = PurePosixPath(page["image_path"]).name
        if (page["image_path"] != url_prefix + "/" + name
                or not name.startswith(f"docx_page_{task_id}_{source_hash[:16]}_") or not name.endswith(".png")):
            return None
        path = output_dir / name
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_PAGE_BYTES:
            return None
        total_bytes += path.stat().st_size
        if total_bytes > MAX_TOTAL_IMAGE_BYTES or hashlib.sha256(path.read_bytes()).hexdigest() != page.get("image_sha256"):
            return None
    result = deepcopy(cached)
    result["cache_reused"] = True
    if result["evidence_kind"] == FOOTER_CACHE_EVIDENCE_KIND:
        _sign_body_render_evidence(result)
    return result


def prepare_docx_source_evidence(
    docx_bytes: bytes, *, output_dir: Path, url_prefix: str, task_id: str,
    check_cancelled: Callable[[], None] = _no_cancel, register_asset: Callable[[str], None],
    renderer_path: str | None = None, cached_evidence: dict | None = None,
) -> dict:
    """Prepare all original Word pages once, with bounded/cancellable local work."""
    check_cancelled()
    report = {"status": "failed", "evidence_kind": EVIDENCE_KIND, "evidence_version": EVIDENCE_VERSION,
              "renderer": "libreoffice", "task_id": task_id,
              "source_sha256": "", "pages": [], "page_images": [], "page_numbers": [], "cache_reused": False, "notes": []}
    created: list[Path] = []
    try:
        rendering_bytes = docx_bytes
        try:
            _validate_source(docx_bytes)
        except _ExternalResourceError:
            rendering_bytes, receipt = _footer_cache_rendering_copy(docx_bytes)
            report.update(evidence_kind=FOOTER_CACHE_EVIDENCE_KIND, evidence_version=FOOTER_CACHE_EVIDENCE_VERSION,
                          evidence_scope="body_only", body_original_same=True,
                          original_sha256=receipt["original_sha256"], derivative_sha256=receipt["derivative_sha256"],
                          rendering_copy_sha256=receipt["derivative_sha256"], footer_cache_receipt=receipt)
        report["source_sha256"] = source_hash = hashlib.sha256(docx_bytes).hexdigest()
        if not isinstance(task_id, str) or not _TASK_TOKEN.fullmatch(task_id):
            raise _EvidenceError("原Word视觉证据的任务标识无效")
        output_dir = Path(output_dir)
        if not output_dir.is_absolute() or not isinstance(url_prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_/-]+", url_prefix):
            raise _EvidenceError("原Word视觉证据的输出位置无效")
        output_dir = output_dir.resolve()
        url_prefix = url_prefix.rstrip("/")
        reused = _reuse_cached(cached_evidence, source_hash, task_id, output_dir, url_prefix,
                               expected_kind=report["evidence_kind"], expected_derivative_sha256=report.get("derivative_sha256"))
        if reused is not None:
            check_cancelled()
            return reused
        renderer = find_docx_renderer(renderer_path)
        if renderer is None:
            report.update(status="unavailable", notes=["未找到本地LibreOffice，暂时无法取得原Word页面图像；请保留人工核对。"])
            return report
        import pymupdf as fitz

        output_dir.mkdir(parents=True, exist_ok=True)
        work_root = SYSTEM_GENERATED_DIR / "docx-source-evidence-work"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="render-", dir=work_root) as folder:
            work = Path(folder)
            original = work / "original.docx"
            original.write_bytes(rendering_bytes)
            pdf_path = _native_pdf(renderer, original, work, check_cancelled)
            font_metadata = work / "font-rendering.json"
            if font_metadata.is_file():
                report.update(json.loads(font_metadata.read_text(encoding="utf-8")))
            with fitz.open(pdf_path) as document:
                if document.needs_pass or not 1 <= len(document) <= MAX_PAGES:
                    raise _EvidenceError("原Word渲染结果加密、无页面或超过40页上限")
                total_pixels = total_bytes = total_text = 0
                nonce = uuid.uuid4().hex[:10]
                for index, page in enumerate(document):
                    check_cancelled()
                    width, height = page.rect.width, page.rect.height
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        raise _EvidenceError("原Word渲染页面尺寸无效")
                    pixels = math.ceil(width / 72 * RENDER_DPI) * math.ceil(height / 72 * RENDER_DPI)
                    total_pixels += pixels
                    if pixels > MAX_PAGE_PIXELS or total_pixels > MAX_TOTAL_PIXELS:
                        raise _EvidenceError("原Word页图超过像素处理上限")
                    text = page.get_text("text", sort=True)
                    total_text += len(text)
                    if len(text) > MAX_PAGE_TEXT or total_text > MAX_TOTAL_TEXT:
                        raise _EvidenceError("原Word页面文字超过证据缓存上限，未截断保存")
                    font_check = _font_visibility(page)
                    pixmap = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
                    data = pixmap.tobytes("png")
                    total_bytes += len(data)
                    if len(data) > MAX_PAGE_BYTES or total_bytes > MAX_TOTAL_IMAGE_BYTES:
                        raise _EvidenceError("原Word页图超过资产缓存大小上限")
                    name = f"docx_page_{task_id}_{source_hash[:16]}_{nonce}_{index + 1}.png"
                    path = output_dir / name
                    created.append(path)
                    path.write_bytes(data)
                    url = url_prefix + "/" + name
                    register_asset(url)
                    check_cancelled()
                    report["pages"].append({"page_number": index + 1, "text": text, "width": width, "height": height,
                                            "image_path": url, "image_sha256": hashlib.sha256(data).hexdigest(),
                                            "pixel_width": pixmap.width, "pixel_height": pixmap.height, "font_check": font_check})
        report.update(status="ready", page_numbers=[page["page_number"] for page in report["pages"]],
                      page_images=[page["image_path"] for page in report["pages"]], render_dpi=RENDER_DPI,
                      manifest_sha256=_manifest_hash(report["pages"]),
                      notes=["页面由LibreOffice直接读取原Word渲染，页码以本次渲染为准，可能与Microsoft Word的分页不同。"])
        if report.get("configured_font_fallbacks"):
            report["notes"].append("本次独立字体配置为宋体/SimSun指定了现有Songti SC作为替代；原Word文件未改写。")
        if report["evidence_kind"] == FOOTER_CACHE_EVIDENCE_KIND:
            report["notes"] = ["本次仅在私有渲染副本中移除了微小页脚缓存图的外链；内嵌缓存、尺寸与位置保留，正文及其他未修改部件逐字节相同。",
                               "证据仅用于核对正文题干与原解，不确认页脚原内容；分页以LibreOffice本次渲染为准，可能不同于Microsoft Word。"]
            if report.get("configured_font_fallbacks"):
                report["notes"].append("宋体/SimSun使用本机现有Songti SC替代字体，原Word文件未改写。")
            _sign_body_render_evidence(report)
            if not validate_docx_body_render_evidence(report):
                raise _EvidenceError("页脚缓存图的正文渲染证据签署未通过")
        return report
    except TaskCancelled:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        for path in created:
            path.unlink(missing_ok=True)
        reason = str(exc) if isinstance(exc, _EvidenceError) else f"本地转换失败（{type(exc).__name__}）"
        report.update(status="failed", pages=[], page_images=[], page_numbers=[], notes=[reason + "；已保留人工核对。"])
        return report
