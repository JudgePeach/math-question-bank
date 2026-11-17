import hashlib
from io import BytesIO
import json
from zipfile import ZipFile

import pymupdf as fitz
import pytest

from mathbank.pdf_mineru_source import load_mineru_pdf_source


def pdf(word):
    with fitz.open() as d:
        page = d.new_page(width=300, height=400)
        page.insert_text((30, 45), word)
        return d.tobytes()


def make_bundle(rows, original):
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("trial_origin.pdf", original)
        archive.writestr("trial_content_list.json", json.dumps(rows))
    return stream.getvalue()


def test_bound_blocks_are_source_text_and_original_image_slots_only():
    original = pdf("Real page")
    rows = [
        {"page_idx": 0, "type": "text", "text": r"1. $a_{n+1}=a_n+\frac1{a_n}$"},
        {"page_idx": 0, "type": "image", "bbox": [200, 300, 500, 600],
         "img_path": "ignored.png", "image_caption": ["图注"]},
        {"page_idx": 0, "type": "page_number", "text": "page 1"},
    ]
    src = load_mineru_pdf_source(original, make_bundle(rows, original))
    assert src.source_sha256 == hashlib.sha256(original).hexdigest()
    page = src.page(0)
    assert r"\frac1{a_n}" in page["markdown"] and "[插图待补: 图1]" in page["markdown"]
    assert "图注" in page["markdown"] and "ignored.png" not in page["markdown"] and "page 1" not in page["markdown"]
    assert page["figures"][0]["bbox"] == {"left": 200, "top": 300, "right": 500, "bottom": 600}


def test_mismatched_source_pixels_are_rejected():
    with pytest.raises(ValueError, match="像素"):
        load_mineru_pdf_source(pdf("Real page"), make_bundle(
            [{"page_idx": 0, "type": "text", "text": "different"}], pdf("Different page")))


@pytest.mark.parametrize("row", [
    {"type": "text", "text": "![](https://example.invalid/p.png)"},
    {"type": "image", "bbox": [800, 20, 40, 50]},
    {"type": "equation", "text": ""},
    {"type": "unknown", "text": "Maybe material"},
])
def test_unsupported_or_unsafe_page_stays_available_to_existing_recognizer(row):
    original = pdf("Real page")
    source = load_mineru_pdf_source(original, make_bundle([{"page_idx": 0, **row}], original))
    assert source.page(0) is None
