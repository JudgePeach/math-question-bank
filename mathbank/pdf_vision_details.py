"""Bounded original-PDF detail views for one page request.

Views overlap intentionally and do not own transcription, figure slots or
question text. All rectangles use the visible rotated page coordinates.
The caller renders locally/serially; workers receive only paths and metadata.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
import math
import os
from pathlib import Path
import re
import stat
from typing import Callable
import uuid

from mathbank.pdf_layout import _page_rect
from mathbank.task_manager import TaskCancelled


DETAIL_DPI = 300
NAVIGATION_DPI = 150
MAX_DETAIL_VIEWS = 2
MAX_DETAIL_SIDE = 2400
MAX_DETAIL_PIXELS = 4_000_000
MAX_DETAIL_PNG_BYTES = 10 * 1024 * 1024
MAX_TOTAL_DETAIL_PIXELS = 8_000_000
MAX_TOTAL_DETAIL_PNG_BYTES = 20 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PORTRAIT_VIEWS = ((0, 0, 1000, 560), (0, 440, 1000, 1000))
_LANDSCAPE_VIEWS = ((0, 0, 560, 1000), (440, 0, 1000, 1000))
_NOTES = {
    "resolution": "局部详情图在尺寸限制后不高于150 DPI，已保留整页识图。",
    "dimensions": "局部详情图的实际尺寸超出安全上限，已保留整页识图。",
    "pixels": "局部详情图超过本页像素额度，已保留整页识图。",
    "png": "局部详情图不是有效PNG，已保留整页识图。",
    "bytes": "局部详情图超过本页PNG大小额度，已保留整页识图。",
    "registration": "局部详情图登记未完成，已撤销本次新图并保留整页识图。",
    "local_failure": "局部详情图生成未完成，已撤销本次新图并保留整页识图。",
}
_DESCRIPTOR_FIELDS = {"id", "page_index", "bbox", "actualdpi", "width", "height", "sha256", "path", "url"}


class _DetailUnavailable(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(_NOTES[code])


def _no_cancel() -> None:
    pass


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _safe_directory(directory: Path, *, create: bool = False) -> None:
    if not directory.is_absolute() or ".." in directory.parts:
        raise ValueError("详情图输出目录必须为可信的绝对目录。")
    # Inspect the supplied spelling before resolving: resolve() alone would
    # conceal a symlink. Callers may supply an explicitly resolved trusted root.
    for part in (*reversed(directory.parents), directory):
        if part.is_symlink() or part.exists() and not part.is_dir():
            raise ValueError("详情图输出路径不能包含符号链接或非目录。")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
        _safe_directory(directory)


def _stat_key(value) -> tuple:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


@dataclass(frozen=True)
class _OwnedFile:
    path: Path
    snapshot: tuple | None = None
    sha256: str | None = None


def _read_snapshot(descriptor: int, *, expected: bytes | None = None) -> tuple[tuple, str]:
    """Read a bounded stable descriptor, optionally proving our written prefix."""
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_DETAIL_PNG_BYTES:
        raise ValueError("详情图所有权快照无效。")
    os.lseek(descriptor, 0, os.SEEK_SET)
    checksum = hashlib.sha256()
    size = 0
    while True:
        data = os.read(descriptor, min(1024 * 1024, MAX_DETAIL_PNG_BYTES - size + 1))
        if not data:
            break
        if size + len(data) > MAX_DETAIL_PNG_BYTES or (
            expected is not None and data != expected[size:size + len(data)]
        ):
            raise ValueError("详情图内容已变化，不能确认归属。")
        checksum.update(data)
        size += len(data)
    after = os.fstat(descriptor)
    if size != before.st_size or _stat_key(before) != _stat_key(after):
        raise ValueError("详情图读取时发生变化，不能确认归属。")
    return _stat_key(after), checksum.hexdigest()


def _freeze_owned_file(path: Path, descriptor: int, expected: bytes) -> _OwnedFile:
    snapshot, checksum = _read_snapshot(descriptor, expected=expected)
    _safe_directory(path.parent)
    current = path.lstat()
    if not stat.S_ISREG(current.st_mode) or _stat_key(current) != snapshot:
        raise ValueError("详情图路径已变化，不能确认归属。")
    return _OwnedFile(path, snapshot, checksum)


def _rollback(created: list[_OwnedFile]) -> None:
    for owned in reversed(created):
        path = owned.path
        if owned.snapshot is None or owned.sha256 is None:
            continue
        try:
            _safe_directory(path.parent)
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or _stat_key(current) != owned.snapshot:
                continue
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            try:
                snapshot, checksum = _read_snapshot(descriptor)
            finally:
                os.close(descriptor)
            # Close before unlink for Windows. Recheck the spelling and complete
            # snapshot afterwards; inode alone can be immediately reused on Linux.
            _safe_directory(path.parent)
            current = path.lstat()
            if (snapshot == owned.snapshot and checksum == owned.sha256
                    and stat.S_ISREG(current.st_mode) and _stat_key(current) == owned.snapshot):
                path.unlink()
        except (OSError, ValueError):
            # A replacement or moved/symlinked parent is no longer our file.
            # Never follow it to delete unrelated assets.
            pass


def render_pdf_detail_views(
    page,
    *,
    page_index: int,
    output_dir: Path,
    asset_prefix: str,
    url_prefix: str,
    register_asset: Callable[[str], object],
    check_cancelled: Callable[[], None] = _no_cancel,
    byte_budget: int = MAX_TOTAL_DETAIL_PNG_BYTES,
) -> dict:
    """Prepare two views atomically; optional failures keep the full-page path.

    Invalid caller configuration raises before writing. Cancellation propagates.
    Ordinary local generation/registration failures return no partial views.
    Existing files, symlinks and replacements are never overwritten or deleted.
    """
    if not callable(register_asset) or not callable(check_cancelled):
        raise ValueError("详情图登记和取消检查必须为可调用对象。")
    check_cancelled()
    if isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0:
        raise ValueError("详情图页码必须为非负整数。")
    if type(getattr(page, "number", None)) is not int or page.number != page_index:
        raise ValueError("详情图页码与整份原PDF的实际页面不一致。")
    if type(byte_budget) is not int or byte_budget < 0:
        raise ValueError("详情图剩余字节额度必须为非负整数。")
    byte_limit = min(MAX_TOTAL_DETAIL_PNG_BYTES, byte_budget)
    directory = Path(output_dir)
    _safe_directory(directory)
    if not isinstance(asset_prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", asset_prefix):
        raise ValueError("详情图文件名前缀无效。")
    if not isinstance(url_prefix, str) or not re.fullmatch(
        r"/static/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+/?", url_prefix
    ):
        raise ValueError("详情图URL前缀必须属于本地静态资源目录。")

    created: list[_OwnedFile] = []
    try:
        import pymupdf as fitz

        if byte_limit == 0:
            raise _DetailUnavailable("bytes")
        visible = _page_rect(page)
        boxes = _LANDSCAPE_VIEWS if visible.width > visible.height else _PORTRAIT_VIEWS
        if len(boxes) > MAX_DETAIL_VIEWS:
            raise _DetailUnavailable("dimensions")
        prepared = []
        total_pixels = total_bytes = 0
        for number, bbox in enumerate(boxes, start=1):
            check_cancelled()
            x0, y0, x1, y1 = bbox
            clip = fitz.Rect(
                visible.x0 + x0 * visible.width / 1000,
                visible.y0 + y0 * visible.height / 1000,
                visible.x0 + x1 * visible.width / 1000,
                visible.y0 + y1 * visible.height / 1000,
            )
            # Leave enough headroom for the integer pixel enclosure of clip.
            scale = min(
                DETAIL_DPI / 72,
                (MAX_DETAIL_SIDE - 2) / clip.width,
                (MAX_DETAIL_SIDE - 2) / clip.height,
                math.sqrt(max(0, MAX_DETAIL_PIXELS - 4 * MAX_DETAIL_SIDE) / clip.get_area()),
            )
            if not math.isfinite(scale) or scale * 72 <= NAVIGATION_DPI + 1e-6:
                raise _DetailUnavailable("resolution")
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB, alpha=False)
            check_cancelled()
            width, height = pixmap.width, pixmap.height
            if min(width, height) < 2 or max(width, height) > MAX_DETAIL_SIDE:
                raise _DetailUnavailable("dimensions")
            pixels = width * height
            if pixels > MAX_DETAIL_PIXELS or total_pixels + pixels > MAX_TOTAL_DETAIL_PIXELS:
                raise _DetailUnavailable("pixels")
            actualdpi = min(width / clip.width, height / clip.height) * 72
            if actualdpi <= NAVIGATION_DPI + 1e-6:
                raise _DetailUnavailable("resolution")
            png = pixmap.tobytes("png")
            del pixmap
            if not isinstance(png, bytes) or not png.startswith(_PNG_SIGNATURE):
                raise _DetailUnavailable("png")
            if len(png) > MAX_DETAIL_PNG_BYTES or total_bytes + len(png) > byte_limit:
                raise _DetailUnavailable("bytes")
            prepared.append((
                {"id": f"p{page_index + 1}_d{number}", "page_index": page_index,
                 "bbox": list(bbox), "actualdpi": round(actualdpi, 4),
                 "width": width, "height": height, "sha256": hashlib.sha256(png).hexdigest()},
                png,
            ))
            total_pixels += pixels
            total_bytes += len(png)

        check_cancelled()
        _safe_directory(directory, create=True)
        views = []
        for descriptor, png in prepared:
            check_cancelled()
            _safe_directory(directory)
            filename = f"{asset_prefix}_{descriptor['id']}_{uuid.uuid4().hex}.png"
            destination = directory / filename
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor_fd = os.open(destination, flags, 0o600)
            pinned_fd = None
            record_index = len(created)
            try:
                identity = os.fstat(descriptor_fd)
                # Even dup/fdopen failure must leave a safely removable empty
                # file. The pin keeps the original inode alive until write/flush
                # has ended, including failures that leave a partial PNG.
                initial = (_OwnedFile(destination, _stat_key(identity), hashlib.sha256(b"").hexdigest())
                           if stat.S_ISREG(identity.st_mode) and identity.st_size == 0 else _OwnedFile(destination))
                created.append(initial)
                pinned_fd = os.dup(descriptor_fd)
                with os.fdopen(descriptor_fd, "wb") as handle:
                    descriptor_fd = None
                    handle.write(png)
            finally:
                try:
                    if descriptor_fd is not None:
                        os.close(descriptor_fd)
                finally:
                    if pinned_fd is not None:
                        try:
                            try:
                                created[record_index] = _freeze_owned_file(destination, pinned_fd, png)
                            except (OSError, ValueError):
                                created[record_index] = _OwnedFile(destination)
                        finally:
                            os.close(pinned_fd)
            owned = created[record_index]
            if owned.snapshot is None or owned.snapshot[2] != len(png) or owned.sha256 != descriptor["sha256"]:
                raise _DetailUnavailable("local_failure")
            check_cancelled()
            url = f"{url_prefix.rstrip('/')}/{filename}"
            registration = register_asset(url)
            if registration is False:
                raise _DetailUnavailable("registration")
            check_cancelled()
            _safe_directory(directory)
            current = destination.lstat()
            if not stat.S_ISREG(current.st_mode) or _stat_key(current) != owned.snapshot:
                raise _DetailUnavailable("registration")
            views.append({**descriptor, "path": str(destination), "url": url})
        # Registration is a caller callback. Before returning, ensure no callback
        # replaced or modified any earlier view, including in-place writes.
        for view, (_descriptor, png) in zip(views, prepared):
            check_cancelled()
            path = Path(view["path"])
            _safe_directory(path.parent)
            descriptor_fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                identity = os.fstat(descriptor_fd)
                owned = next((item for item in created if item.path == path), None)
                if (owned is None or not stat.S_ISREG(identity.st_mode)
                        or _stat_key(identity) != owned.snapshot
                        or identity.st_size != len(png)):
                    raise _DetailUnavailable("registration")
                with os.fdopen(descriptor_fd, "rb") as handle:
                    descriptor_fd = None
                    actual = handle.read(len(png) + 1)
                if actual != png:
                    raise _DetailUnavailable("registration")
            finally:
                if descriptor_fd is not None:
                    os.close(descriptor_fd)
        check_cancelled()
        return {"status": "prepared", "views": views, "notes": [], "total_png_bytes": total_bytes}
    except TaskCancelled:
        _rollback(created)
        raise
    except Exception as error:
        _rollback(created)
        code = error.code if isinstance(error, _DetailUnavailable) else "local_failure"
        return {"status": "skipped", "views": [], "notes": [_NOTES[code]], "total_png_bytes": 0}


def prepare_detail_messages(
    views: list[dict], *, page_index: int,
    check_cancelled: Callable[[], None] = _no_cancel,
) -> tuple[list[dict], dict]:
    """Validate every internally registered view before building one request.

    The caller must bind these descriptors to its own task's registered assets;
    neither an API client nor a model may supply descriptors or local paths.
    This helper enforces the renderer contract and image snapshot, not ownership
    of an arbitrary filesystem tree. Failure raises with no partial messages.
    """
    if not callable(check_cancelled):
        raise ValueError("详情图取消检查必须为可调用对象。")
    check_cancelled()
    if isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0:
        raise ValueError("详情图页码必须为非负整数。")
    if not isinstance(views, list) or len(views) > MAX_DETAIL_VIEWS:
        raise ValueError("单页详情图数量或格式无效。")
    import base64
    from PIL import Image

    checked, identifiers, identities, paths = [], set(), set(), set()
    total_pixels = total_bytes = 0
    axis = None
    for view in views:
        check_cancelled()
        if not isinstance(view, dict) or set(view) != _DESCRIPTOR_FIELDS:
            raise ValueError("详情图描述字段无效。")
        identifier = view["id"]
        if (not isinstance(identifier, str) or identifier not in (f"p{page_index + 1}_d1", f"p{page_index + 1}_d2")
                or identifier in identifiers or type(view["page_index"]) is not int or view["page_index"] != page_index):
            raise ValueError("详情图标识重复、无效或与原页不一致。")
        bbox = view["bbox"]
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(not _finite_number(number) for number in bbox)):
            raise ValueError("详情图原页范围无效。")
        number = int(identifier[-1]) - 1
        this_axis = ("portrait" if tuple(bbox) == _PORTRAIT_VIEWS[number]
                     else "landscape" if tuple(bbox) == _LANDSCAPE_VIEWS[number] else None)
        if this_axis is None or axis is not None and this_axis != axis:
            raise ValueError("详情图范围不属于同一原页的已登记视图。")
        axis = this_axis
        width, height, actualdpi = view["width"], view["height"], view["actualdpi"]
        if (type(width) is not int or type(height) is not int or min(width, height) < 2
                or max(width, height) > MAX_DETAIL_SIDE or width * height > MAX_DETAIL_PIXELS
                or not _finite_number(actualdpi) or not NAVIGATION_DPI < actualdpi <= DETAIL_DPI * 2):
            raise ValueError("详情图像素、尺寸或有效分辨率无效。")
        digest = view["sha256"]
        path_value, url = view["path"], view["url"]
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(path_value, str) or not isinstance(url, str)):
            raise ValueError("详情图快照或本地路径格式无效。")
        path = Path(path_value)
        _safe_directory(path.parent)
        if (path.is_symlink() or not path.is_file()
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}_" + re.escape(identifier) + r"_[0-9a-f]{32}\.png", path.name)
                or not re.fullmatch(r"/static/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\.png", url)
                or Path(url).name != path.name or path in paths):
            raise ValueError("详情图路径、URL或文件标识无效。")
        descriptor_fd = None
        try:
            descriptor_fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            file_stat = os.fstat(descriptor_fd)
            identity = (file_stat.st_dev, file_stat.st_ino)
            if (not stat.S_ISREG(file_stat.st_mode) or identity in identities
                    or file_stat.st_size > MAX_DETAIL_PNG_BYTES):
                raise ValueError("详情图文件重复或超过大小额度。")
            with os.fdopen(descriptor_fd, "rb") as handle:
                descriptor_fd = None
                png = handle.read(MAX_DETAIL_PNG_BYTES + 1)
            if (len(png) != file_stat.st_size or len(png) > MAX_DETAIL_PNG_BYTES
                    or hashlib.sha256(png).hexdigest() != digest or not png.startswith(_PNG_SIGNATURE)):
                raise ValueError("详情图文件已变化或超过大小额度。")
            with Image.open(BytesIO(png)) as image:
                if image.format != "PNG" or image.size != (width, height) or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("详情图真实格式或尺寸与描述不一致。")
                image.verify()
            with Image.open(BytesIO(png)) as image:
                image.load()
        except TaskCancelled:
            raise
        except Exception as error:
            raise ValueError("详情图读取、PNG解析或快照校验未通过。") from error
        finally:
            if descriptor_fd is not None:
                os.close(descriptor_fd)
        check_cancelled()
        total_pixels += width * height
        total_bytes += len(png)
        if total_pixels > MAX_TOTAL_DETAIL_PIXELS or total_bytes > MAX_TOTAL_DETAIL_PNG_BYTES:
            raise ValueError("详情图合计像素或PNG大小超过本页额度。")
        identifiers.add(identifier)
        identities.add(identity)
        paths.add(path)
        safe_metadata = {key: view[key] for key in ("id", "page_index", "actualdpi", "width", "height", "sha256")}
        safe_metadata["bbox"] = list(view["bbox"])
        checked.append((safe_metadata, png))

    check_cancelled()
    messages = []
    for view, png in checked:
        check_cancelled()
        bbox = view["bbox"]
        label = (f"PDF_DETAIL_VIEW id={view['id']} 原PDF页号={page_index + 1} "
                 f"原页范围 left={bbox[0]}, top={bbox[1]}, right={bbox[2]}, bottom={bbox[3]}。"
                 "这是同一原页的放大辅助视图，只精读、不另抄。所有输出坐标仍以整页左上角为原点、0..1000为唯一基准。")
        messages.extend([
            {"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}},
        ])
    metadata = {"detail_count": len(checked), "detail_total_pixels": total_pixels,
                "detail_total_png_bytes": total_bytes, "detail_views": [view for view, _png in checked]}
    check_cancelled()
    return messages, metadata
