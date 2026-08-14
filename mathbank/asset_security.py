"""Security helpers for uploaded assets and private local files.

The browser is allowed to refer to uploaded images by URL, but those references
must never become arbitrary filesystem paths.  This module is the single place
that translates an upload URL into a local file and validates its containment,
type and symlink status.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SINGLE_IMAGE_BYTES = 10 * 1024 * 1024
MAX_OCR_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000
MAX_NORMALIZED_IMAGE_BYTES = 20 * 1024 * 1024

SAFE_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_SAFE_ASSET_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class AssetSecurityError(ValueError):
    """Raised when a client-supplied asset reference leaves the upload root."""


class UploadTooLargeError(ValueError):
    """Raised as soon as a streamed upload exceeds its endpoint limit."""


class InvalidImageError(ValueError):
    """Raised when uploaded bytes are not a safe supported raster image."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    extension: str
    media_type: str
    width: int
    height: int


def _reference_parts(reference: str, url_prefix: str) -> tuple[str, ...]:
    if not isinstance(reference, str) or not reference.strip():
        raise AssetSecurityError("插图路径不能为空。")

    normalized = reference.strip().replace("\\", "/")
    if "\x00" in normalized or "?" in normalized or "#" in normalized:
        raise AssetSecurityError("插图路径包含不允许的字符。")
    if _URI_SCHEME.match(normalized):
        raise AssetSecurityError("插图路径必须是本地上传资源，不能是外部 URI。")

    normalized = normalized.lstrip("/")
    prefix = str(PurePosixPath(str(url_prefix).strip().replace("\\", "/").strip("/")))
    if normalized == prefix:
        relative = ""
    elif normalized.startswith(prefix + "/"):
        relative = normalized[len(prefix) + 1 :]
    elif "/" not in normalized:
        # Internal export helpers may pass a generated basename directly.
        relative = normalized
    else:
        raise AssetSecurityError("插图路径不属于当前上传目录。")

    pure_path = PurePosixPath(relative)
    parts = pure_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise AssetSecurityError("插图路径包含目录穿越片段。")
    if any(not _SAFE_ASSET_COMPONENT.fullmatch(part) for part in parts):
        raise AssetSecurityError("插图路径包含不安全的文件名。")
    return parts


def resolve_upload_asset(
    reference: str,
    *,
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    require_file: bool = True,
    allowed_extensions: Iterable[str] = SAFE_IMAGE_EXTENSIONS,
) -> Path:
    """Resolve a browser upload URL to a contained, non-symlink local path."""

    root = Path(uploads_dir).resolve()
    parts = _reference_parts(reference, url_prefix)
    candidate = root.joinpath(*parts)

    # Reject a symlink at any level below the trusted upload root.  Merely
    # checking the final resolved path would otherwise accept an in-root link.
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise AssetSecurityError("插图路径不能经过符号链接。")

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AssetSecurityError("插图路径越出了上传目录。") from exc

    extension = resolved.suffix.lower()
    normalized_extensions = {str(value).lower() for value in allowed_extensions}
    if extension not in normalized_extensions:
        raise AssetSecurityError("插图文件类型不在安全白名单中。")

    if resolved.exists() and not resolved.is_file():
        raise AssetSecurityError("插图路径不是普通文件。")
    if require_file and (not resolved.exists() or not resolved.is_file()):
        raise AssetSecurityError("插图文件不存在或不是普通文件。")
    return resolved


def normalize_upload_asset_reference(
    reference: str,
    *,
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    require_file: bool = True,
) -> str:
    """Return the canonical browser URL for one validated upload asset."""

    root = Path(uploads_dir).resolve()
    resolved = resolve_upload_asset(
        reference,
        uploads_dir=root,
        url_prefix=url_prefix,
        require_file=require_file,
    )
    relative = resolved.relative_to(root).as_posix()
    return f"/{str(PurePosixPath(str(url_prefix).strip('/')))}/{relative}"


def normalize_upload_asset_references(
    references,
    *,
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    require_file: bool = True,
    max_items: int = 50,
) -> list[str]:
    """Validate and deduplicate the image-path array stored on a question."""

    if not isinstance(references, list):
        raise AssetSecurityError("image_paths 必须是插图路径数组。")
    if len(references) > max_items:
        raise AssetSecurityError(f"单题插图数量不能超过 {max_items} 张。")

    normalized: list[str] = []
    seen: set[str] = set()
    for reference in references:
        value = normalize_upload_asset_reference(
            reference,
            uploads_dir=uploads_dir,
            url_prefix=url_prefix,
            require_file=require_file,
        )
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def read_stream_limited(stream, max_bytes: int, *, chunk_size: int = 1024 * 1024) -> bytes:
    """Read a synchronous file-like object without ever buffering past the cap."""

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(chunk_size, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(f"上传文件超过 {max_bytes} 字节安全上限。")
        chunks.append(chunk)
    return b"".join(chunks)


def normalize_raster_image(
    raw: bytes,
    *,
    max_pixels: int = MAX_IMAGE_PIXELS,
    max_output_bytes: int = MAX_NORMALIZED_IMAGE_BYTES,
) -> NormalizedImage:
    """Fully decode and re-encode an uploaded raster into passive image bytes."""

    if not raw:
        raise InvalidImageError("图片内容为空。")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise InvalidImageError("图片像素尺寸超过安全上限。")
            if (source.format or "").upper() not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise InvalidImageError("仅支持 PNG、JPEG、GIF 或 WebP 图片。")

            # Decode the complete first frame.  Re-encoding removes metadata,
            # trailing polyglot payloads and active content disguised by suffix.
            source.seek(0)
            source.load()
            transposed = ImageOps.exif_transpose(source)
            has_alpha = transposed.mode in {"RGBA", "LA"} or (
                transposed.mode == "P" and "transparency" in transposed.info
            )
            safe_image = transposed.convert("RGBA" if has_alpha else "RGB")

            output = io.BytesIO()
            if (source.format or "").upper() == "JPEG" and not has_alpha:
                safe_image.save(output, format="JPEG", quality=95, optimize=True)
                extension = ".jpg"
                media_type = "image/jpeg"
            else:
                safe_image.save(output, format="PNG", optimize=True)
                extension = ".png"
                media_type = "image/png"
            normalized = output.getvalue()
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("文件内容不是有效且安全的栅格图片。") from exc

    if len(normalized) > max_output_bytes:
        raise InvalidImageError("图片安全转码后的体积超过上限。")
    return NormalizedImage(
        data=normalized,
        extension=extension,
        media_type=media_type,
        width=width,
        height=height,
    )


def harden_private_path(path: str | os.PathLike, *, directory: bool = False) -> None:
    """Best-effort owner-only permissions for local secrets."""

    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        # Windows and some packaged filesystems do not implement POSIX modes.
        pass


def write_private_text_atomic(path: str | os.PathLike, text: str) -> None:
    """Atomically write UTF-8 text with owner-only permissions."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            try:
                fchmod(fd, 0o600)
            except (OSError, NotImplementedError):
                pass
        handle = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        harden_private_path(target)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            if os.path.exists(temp_name):
                os.remove(temp_name)
        except OSError:
            pass
