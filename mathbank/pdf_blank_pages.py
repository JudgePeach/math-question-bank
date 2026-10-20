"""Conservative, local evidence for genuinely empty PDF pages."""

MAX_BLANK_PAGE_PIXELS = 30_000_000


def is_confirmed_blank_page(page, *, pixmap=None) -> bool:
    """Skip only pages with no text/annotations and an entirely white render.

    An empty OCR response is never evidence of an empty page. Unknown drawing
    resources and unreadable pages keep the ordinary recognition path.
    """
    try:
        if page.get_text("text").strip() or next(page.annots(), None) is not None:
            return False
        if next(page.widgets(), None) is not None:
            return False
        if pixmap is None:
            pixels = (page.rect.width * 150 / 72) * (page.rect.height * 150 / 72)
            if pixels > MAX_BLANK_PAGE_PIXELS:
                return False
            pixmap = page.get_pixmap(dpi=150, alpha=False)
        if pixmap.n != 3 or pixmap.alpha or pixmap.width * pixmap.height > MAX_BLANK_PAGE_PIXELS:
            return False
        samples = pixmap.samples
        # Exact white is deliberate: faint ink, gray scan noise and tiny marks
        # cannot be silently discarded as a near-white blank sheet.
        return bool(samples) and samples.count(255) == len(samples)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return False
