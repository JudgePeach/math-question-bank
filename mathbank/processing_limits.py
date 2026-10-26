"""Bounded resource limits, resolved after the application's environment load.

Defaults preserve existing operation. Overrides require a restart and never
disable image decoding, path validation, cancellation or evidence checks.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import os
import warnings

MIB = 1024 * 1024


@dataclass(frozen=True)
class ProcessingLimits:
    image_upload_bytes: int = 10 * MIB
    image_pixels: int = 30_000_000
    normalized_image_bytes: int = 20 * MIB
    document_upload_bytes: int = 30 * MIB
    text_source_bytes: int = 5 * MIB
    pdf_pages: int = 80
    document_workers: int = 2
    document_queue: int = 4
    classify_images: int = 4
    classify_image_bytes: int = 4 * MIB
    pdf_ocr_workers: int = 4


# name, default, minimum, maximum, unit multiplier
LIMIT_SPECS = {
    "image_upload_bytes": ("MATHBANK_MAX_IMAGE_MIB", 10, 1, 64, MIB),
    "image_pixels": ("MATHBANK_MAX_IMAGE_MEGAPIXELS", 30, 1, 120, 1_000_000),
    "normalized_image_bytes": ("MATHBANK_MAX_NORMALIZED_IMAGE_MIB", 20, 1, 64, MIB),
    "document_upload_bytes": ("MATHBANK_MAX_DOCUMENT_MIB", 30, 1, 256, MIB),
    "text_source_bytes": ("MATHBANK_MAX_TEXT_SOURCE_MIB", 5, 1, 32, MIB),
    "pdf_pages": ("MATHBANK_MAX_PDF_PAGES", 80, 1, 500, 1),
    "document_workers": ("MATHBANK_DOCUMENT_WORKERS", 2, 1, 4, 1),
    "document_queue": ("MATHBANK_DOCUMENT_QUEUE", 4, 0, 64, 1),
    "classify_images": ("MATHBANK_CLASSIFY_MAX_IMAGES", 4, 1, 12, 1),
    "classify_image_bytes": ("MATHBANK_CLASSIFY_IMAGE_MIB", 4, 1, 16, MIB),
    "pdf_ocr_workers": ("PDF_OCR_CONCURRENCY", 4, 1, 12, 1),
}


def bounded_integer(environment, name, default, minimum, maximum):
    raw = environment.get(name)
    if raw is None:
        return default
    value = raw.strip() if isinstance(raw, str) else ""
    if (1 <= len(value) <= len(str(maximum)) and value.isascii()
            and value.isdecimal() and minimum <= int(value) <= maximum):
        return int(value)
    warnings.warn(f"{name} must be an integer from {minimum} to {maximum}; "
                  f"using default {default}.", RuntimeWarning, stacklevel=3)
    return default


def resolve_processing_limits(environ: Mapping[str, str] | None = None) -> ProcessingLimits:
    environment = os.environ if environ is None else environ
    return ProcessingLimits(**{field: bounded_integer(environment, name, default, low, high) * unit
        for field, (name, default, low, high, unit) in LIMIT_SPECS.items()})


LIMITS = resolve_processing_limits()
CLASSIFY_IMAGES = LIMITS.classify_images
CLASSIFY_IMAGE_BYTES = LIMITS.classify_image_bytes


def public_processing_limits():
    return asdict(LIMITS)
