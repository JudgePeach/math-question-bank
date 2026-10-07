"""Only proved opaque overpaint can reject native source; never edit its text."""
from functools import partial
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from mathbank import pdf_inspector_helper as helper

OLD = "OLD_VALUE=999"
VISIBLE = "VISIBLE_VALUE=1"
MARKDOWN = OLD + "\n" + VISIBLE


def painted_page(document, kind="after"):
    page = document.new_page(width=400, height=300)
    rectangle = fitz.Rect(30, 60, 350, 90)
    if kind in {"before", "background"}:
        page.draw_rect(rectangle if kind == "before" else page.rect, color=None, fill=(1, 1, 1))
    page.insert_text((40, 80), OLD, fontsize=12)
    if kind in {"after", "partial", "transparent", "clip", "blend", "soft_mask", "rotation", "crop", "layer"}:
        if kind == "partial":
            rectangle = fitz.Rect(30, 60, 80, 90)
        opacity = .4 if kind == "transparent" else 1
        page.draw_rect(rectangle, color=None, fill=(1, 1, 1), fill_opacity=opacity)
        path_xref = page.get_contents()[-1]
        if kind == "clip":
            stream = document.xref_stream(path_xref)
            document.update_stream(path_xref, stream.replace(b"q", b"q\n0 0 70 300 re W n", 1))
        if kind in {"blend", "soft_mask"}:
            resources = int(document.xref_get_key(page.xref, "Resources")[1].split()[0])
            state = document.get_new_xref()
            document.update_object(state, "<< /Type /ExtGState /BM /Multiply >>" if kind == "blend" else
                                   "<< /Type /ExtGState /SMask << /S /Alpha >> >>")
            document.xref_set_key(resources, "ExtGState", f"<< /Unsafe {state} 0 R >>")
            stream = document.xref_stream(path_xref)
            document.update_stream(path_xref, stream.replace(b"q", b"q\n/Unsafe gs", 1))
    elif kind == "stroke":
        page.draw_rect(rectangle, color=(1, 1, 1), fill=None)
    elif kind == "nonrect":
        page.draw_polyline([(30, 60), (350, 60), (350, 90), (30, 60)], fill=(1, 1, 1))
    elif kind == "math_overlay":
        page.draw_line((46, 62), (50, 84), width=.5)
    page.insert_text((40, 80), VISIBLE, fontsize=12)
    if kind == "rotation":
        page.set_rotation(90)
    elif kind == "crop":
        page.set_cropbox(fitz.Rect(10, 10, 380, 280))
    elif kind == "layer":
        layer = document.add_ocg("optional")
        page.draw_rect(rectangle, color=None, fill=(1, 1, 1), oc=layer)
    return page


def test_later_opaque_rectangle_plus_exact_unique_source_text_rejects_native_baseline():
    with fitz.open() as document:
        page = painted_page(document)
        evidence = helper._native_covered_text_evidence(page)
        assert OLD in evidence and VISIBLE not in evidence
        assert helper._native_paint_visibility_reasons(MARKDOWN, page)
        assert helper._native_paint_visibility_reasons(MARKDOWN, evidence=evidence)
        assert MARKDOWN == OLD + "\n" + VISIBLE


@pytest.mark.parametrize("kind", ["before", "background", "partial", "transparent", "stroke", "nonrect", "clip", "blend",
                                  "soft_mask", "math_overlay", "rotation", "crop", "layer"])
def test_uncertain_or_noncovering_paint_does_not_reject_or_delete_source(kind):
    with fitz.open() as document:
        page = painted_page(document, kind)
        assert helper._native_paint_visibility_reasons(MARKDOWN, page) == []


def test_native_reader_that_already_omitted_the_covered_text_is_not_penalized():
    with fitz.open() as document:
        assert helper._native_paint_visibility_reasons(VISIBLE, painted_page(document)) == []


def test_nonunique_markdown_occurrence_cannot_establish_the_covered_source_owner():
    with fitz.open() as document:
        assert helper._native_paint_visibility_reasons(MARKDOWN + "\n" + OLD, painted_page(document)) == []


def test_form_depth_is_not_assumed_known():
    with fitz.open() as source, fitz.open() as document:
        painted_page(source)
        page = document.new_page(width=400, height=300)
        page.show_pdf_page(page.rect, source, 0)
        assert page.get_xobjects()
        assert helper._native_paint_visibility_reasons(MARKDOWN, page) == []


def test_inherited_page_resources_are_unknown_not_assumed_normal():
    with fitz.open() as document:
        page = painted_page(document)
        kind, resources = document.xref_get_key(page.xref, "Resources")
        assert kind == "xref"
        parent = int(document.xref_get_key(page.xref, "Parent")[1].split()[0])
        document.xref_set_key(parent, "Resources", resources)
        document.xref_set_key(page.xref, "Resources", "null")
        assert helper._native_paint_visibility_reasons(MARKDOWN, page) == []


@pytest.mark.parametrize("budget", ["_PAINT_MAX_PATHS", "_PAINT_MAX_COVERS", "_PAINT_MAX_SPANS", "_PAINT_MAX_CHARS",
                                    "_PAINT_MAX_COMPARISONS", "_PAINT_MAX_SECONDS"])
def test_work_budget_expiration_is_unknown_not_invented_occlusion(monkeypatch, budget):
    with fitz.open() as document:
        page = painted_page(document)
        monkeypatch.setattr(helper, budget, -1 if budget == "_PAINT_MAX_SECONDS" else 0)
        assert helper._native_paint_visibility_reasons(MARKDOWN, page) == []


def test_invalid_trace_or_drawing_data_cannot_prove_a_cover():
    with fitz.open() as document:
        page = painted_page(document)
        class BadTrace:
            def __getattr__(self, name):
                return getattr(page, name)
            def get_texttrace(self):
                return [{"bbox": [float("nan"), 0, 1, 1], "seqno": 0, "chars": []}]
        assert helper._native_paint_visibility_reasons(MARKDOWN, BadTrace()) == []


def mocked_inspector(monkeypatch, markdown=MARKDOWN, pages=None):
    raw_pages = pages or [SimpleNamespace(page=0, markdown=markdown, needs_ocr=False)]
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", SimpleNamespace(
        extract_pages_markdown=lambda *_args, **_kwargs: SimpleNamespace(pages=raw_pages),
        extract_text_with_positions=lambda *_args, **_kwargs: []))


def forbid_recertification(monkeypatch):
    from mathbank import pdf_native_math
    monkeypatch.setattr(pdf_native_math, "repair_native_page", lambda *_args: {
        "status": "repaired", "markdown": MARKDOWN, "notes": [], "stats": {}})


def test_per_page_gate_and_post_repair_recheck_share_one_owned_document(monkeypatch, tmp_path):
    with fitz.open() as document:
        painted_page(document)
        source = document.tobytes()
    mocked_inspector(monkeypatch)
    forbid_recertification(monkeypatch)
    actual_temp = helper.tempfile.NamedTemporaryFile
    monkeypatch.setattr(helper.tempfile, "NamedTemporaryFile", partial(actual_temp, dir=tmp_path))
    actual_open = helper.fitz.open
    opens = []
    def open_pdf(*args, **kwargs):
        opens.append(1)
        return actual_open(*args, **kwargs)
    monkeypatch.setattr(helper.fitz, "open", open_pdf)
    result = helper.inspect_and_extract_pdf(source)
    page = result["pages"][0]
    assert opens == [1]
    assert page["needs_ocr"] is True
    assert page["markdown"] == MARKDOWN
    assert page["native_repair"]["status"] == "fallback"
    assert helper._PAINT_VISIBILITY_REASON in page["quality_reasons"]
    assert result["markdown"] is None


def test_pymupdf_fallback_uses_the_same_cover_gate_and_cannot_be_recertified(monkeypatch):
    with fitz.open() as document:
        painted_page(document)
        source = document.tobytes()
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", False)
    forbid_recertification(monkeypatch)
    result = helper.inspect_and_extract_pdf(source)
    assert result["pages"][0]["needs_ocr"] is True
    assert OLD in result["pages"][0]["markdown"]
    assert helper._PAINT_VISIBILITY_REASON in result["pages"][0]["quality_reasons"]
    assert result["pages"][0]["native_repair"]["status"] == "fallback"


def test_legacy_aggregate_checks_the_selected_second_page_without_replacing_all_text(monkeypatch, tmp_path):
    with fitz.open() as document:
        page = document.new_page(width=400, height=300)
        page.insert_text((40, 80), "FIRST_PAGE_RELIABLE_TEXT_WITHOUT_ANY_PAINT", fontsize=12)
        painted_page(document)
        source = document.tobytes()
    aggregate = "FIRST_PAGE_RELIABLE_TEXT_WITHOUT_ANY_PAINT\n" + MARKDOWN
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", SimpleNamespace(
        process_pdf=lambda *_args, **_kwargs: SimpleNamespace(pdf_type="text_based", markdown=aggregate,
                                                            has_encoding_issues=False, pages_needing_ocr=[]),
        extract_text_with_positions=lambda *_args, **_kwargs: []))
    actual_temp = helper.tempfile.NamedTemporaryFile
    monkeypatch.setattr(helper.tempfile, "NamedTemporaryFile", partial(actual_temp, dir=tmp_path))
    result = helper.inspect_and_extract_pdf(source, page_indices=[0, 1])
    assert result["pages"][0]["needs_ocr"] is True
    assert result["pages"][0]["markdown"] == aggregate
    assert result["pages"][0]["source"] == "pdf-inspector-legacy"
    assert helper._PAINT_VISIBILITY_REASON in result["pages"][0]["quality_reasons"]
    assert "native_repair" not in result["pages"][0]
