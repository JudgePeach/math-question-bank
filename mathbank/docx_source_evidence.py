"""Bounded native DOCX page evidence, never a rendering of extracted Markdown.

LibreOffice reads an untouched private copy of the uploaded Word document in
an isolated profile. Only bounded page PNGs survive the temporary conversion;
the caller owns their task registration and lifetime. No model is invoked.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
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
_TASK_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_DYNAMIC_FIELDS = re.compile(r"\b(?:DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|DATABASE)\b", re.IGNORECASE)


class _EvidenceError(ValueError):
    """Only fixed local messages may be returned in ordinary task notes."""


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
                            raise _EvidenceError("原Word含需读取外部资源的链接，无法生成独立只读视觉证据")
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


def _reuse_cached(cached: dict | None, source_hash: str, task_id: str, output_dir: Path, url_prefix: str) -> dict | None:
    if (not isinstance(cached, dict) or cached.get("status") != "ready" or cached.get("source_sha256") != source_hash
            or cached.get("task_id") != task_id or cached.get("evidence_kind") != EVIDENCE_KIND
            or cached.get("evidence_version") != EVIDENCE_VERSION):
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
        _validate_source(docx_bytes)
        report["source_sha256"] = source_hash = hashlib.sha256(docx_bytes).hexdigest()
        if not isinstance(task_id, str) or not _TASK_TOKEN.fullmatch(task_id):
            raise _EvidenceError("原Word视觉证据的任务标识无效")
        output_dir = Path(output_dir)
        if not output_dir.is_absolute() or not isinstance(url_prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_/-]+", url_prefix):
            raise _EvidenceError("原Word视觉证据的输出位置无效")
        output_dir = output_dir.resolve()
        url_prefix = url_prefix.rstrip("/")
        reused = _reuse_cached(cached_evidence, source_hash, task_id, output_dir, url_prefix)
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
            original.write_bytes(docx_bytes)
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
