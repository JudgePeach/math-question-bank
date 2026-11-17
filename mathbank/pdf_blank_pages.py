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


def is_confirmed_number_only_page(page) -> bool:
    """Prove a pale header and one isolated centered footer page number."""
    try:
        if (page.rotation or next(page.annots(), None) is not None
                or next(page.widgets(), None) is not None):
            return False
        if page.get_text("text").strip() != str(page.number + 1):
            return False
        blocks = [b for b in page.get_text("blocks") if len(b) >= 7 and b[6] == 0 and b[4].strip()]
        if len(blocks) != 1:
            return False
        x0, y0, x1, y1 = blocks[0][:4]
        width, height = page.rect.width, page.rect.height
        if (not .92 * height < y0 < y1 <= height or not .42 * width < x0 < x1 < .58 * width
                or x1 - x0 > .05 * width or y1 - y0 > .04 * height
                or width * height * (150 / 72) ** 2 > MAX_BLANK_PAGE_PIXELS):
            return False
        pix = page.get_pixmap(dpi=150, alpha=False)
        if pix.n != 3 or pix.alpha or pix.x or pix.y or pix.width * pix.height > MAX_BLANK_PAGE_PIXELS:
            return False
        stride = pix.width * pix.n
        top_end = int(.085 * pix.height)
        before_footer = max(top_end, int(y0 / height * pix.height) - 5)
        after_footer = min(pix.height, int(y1 / height * pix.height) + 6)
        left = max(0, int(x0 / width * pix.width) - 5) * pix.n
        right = min(pix.width, int(x1 / width * pix.width) + 6) * pix.n
        samples = pix.samples
        # Light header decoration can occupy only the top sliver. The entire
        # remaining plane must be white except for the exact footer glyph box.
        if min(samples[:top_end * stride], default=255) < 215:
            return False
        for section in (samples[top_end * stride:before_footer * stride], samples[after_footer * stride:]):
            if section.count(255) != len(section):
                return False
        for row in range(before_footer, after_footer):
            offset = row * stride
            if (samples[offset:offset + left].count(255) != left
                    or samples[offset + right:offset + stride].count(255) != stride - right):
                return False
        return True
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return False
