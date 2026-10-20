"""Reuse navigation PNGs only after the checkpoint's asset receipts validate.

These are pixels rendered from the original PDF, not accepted OCR or proof of
mathematical correctness. Missing or ambiguous legacy bindings are a cache miss.
"""
import re


_NAVIGATION = re.compile(
    r"/static/(?:uploads|test_uploads)/tmp/pdf_page_"
    r"[A-Za-z0-9_-]{1,160}_(0|[1-9][0-9]*)\.png"
)


def cached_pdf_navigation(checkpoint, page_index, *, resolve_asset):
    """Return (URL, path) from an already validated server-owned checkpoint.

    Call only after prepare_pdf_checkpoint verified source/settings, the sealed
    manifest and every asset's full SHA-256. Do not accept browser-supplied URLs.
    New checkpoints bind the source, physical page and DPI explicitly; old ones
    can use an exact layout page or a unique server-generated navigation name.
    """
    if (type(page_index) is not int or page_index < 0
            or page_index not in checkpoint.get("page_indices", [])):
        return None
    assets = checkpoint.get("assets", {})
    binding = checkpoint.get("navigation_pages", {}).get(str(page_index))
    if binding is not None:
        if (not isinstance(binding, dict) or binding.get("page_index") != page_index
                or binding.get("dpi") != 150
                or binding.get("document_sha256") != checkpoint.get("document_sha256")):
            return None
        candidates = [binding.get("url")]
    else:
        pages = ((checkpoint.get("prepared") or {}).get("layout_result") or {}).get("pages", [])
        candidates = [row.get("page_image") for row in pages
                      if isinstance(row, dict) and row.get("page_index") == page_index]
        if not candidates:
            candidates = [url for url in assets if isinstance(url, str)
                          and (match := _NAVIGATION.fullmatch(url))
                          and int(match.group(1)) == page_index]
    candidates = set(url for url in candidates if isinstance(url, str))
    if len(candidates) != 1:
        return None
    url = candidates.pop()
    match = _NAVIGATION.fullmatch(url)
    if not match or int(match.group(1)) != page_index or url not in assets:
        return None
    path = resolve_asset(url)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != assets[url].get("size"):
        return None
    return url, path


def bind_pdf_navigation(checkpoint, page_index, url):
    """Bind a locally rendered/reused navigation page before sealing the state."""
    match = _NAVIGATION.fullmatch(url)
    if (not match or type(page_index) is not int or int(match.group(1)) != page_index
            or page_index not in checkpoint["page_indices"]):
        raise ValueError("PDF 原页图与物理页码不一致。")
    checkpoint.setdefault("navigation_pages", {})[str(page_index)] = {
        "page_index": page_index, "url": url, "dpi": 150,
        "document_sha256": checkpoint["document_sha256"],
    }
