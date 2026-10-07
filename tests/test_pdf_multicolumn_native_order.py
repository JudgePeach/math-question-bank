"""Physical lane order plus synthesized table evidence, never numeric sorting."""
from functools import partial
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from mathbank import pdf_inspector_helper as helper


def prose(number):
    return f"{number}. 已知条件请依据原题完整求解并保留选项。"


def columns(document, *, numbers=(1, 2, 3, 4), table=False, rotation=0, duplicate=False):
    page = document.new_page(width=600, height=800)
    for number, x, y in zip(numbers, (40, 40, 330, 330), (100, 260, 100, 260)):
        page.insert_text((x, y), prose(number), fontname="china-s", fontsize=10)
    if duplicate:
        page.insert_text((40, 410), prose(numbers[0]), fontname="china-s", fontsize=10)
    if table:
        page.draw_rect(fitz.Rect(30, 75, 575, 300), width=.6)
        page.draw_line((300, 75), (300, 300), width=.6)
        page.draw_line((30, 190), (575, 190), width=.6)
    page.set_rotation(rotation)
    return page


def markdown_table(order=(2, 4, 1, 3)):
    return "\n".join([
        "|" + prose(order[0]) + "|" + prose(order[1]) + "|",
        "|---|---|",
        "|" + prose(order[2]) + "|" + prose(order[3]) + "|",
    ])


def reasons(page, markdown):
    return helper._native_table_position_reasons(markdown, page)


def test_two_independent_physical_lanes_inverted_inside_a_false_table_are_rejected():
    with fitz.open() as document:
        page = columns(document)
        assert reasons(page, markdown_table())


def test_inversion_proof_does_not_require_ascending_question_numbers():
    with fitz.open() as document:
        page = columns(document, numbers=(10, 8, 9, 7))
        assert reasons(page, markdown_table((8, 7, 10, 9)))


@pytest.mark.parametrize("order", [(1, 3, 2, 4), (1, 2, 3, 4)])
def test_two_columns_with_preserved_local_order_do_not_guess_a_global_reading_order(order):
    with fitz.open() as document:
        assert reasons(columns(document), markdown_table(order)) == []


@pytest.mark.parametrize("order", [(10, 9, 8, 7), (10, 8, 9, 7)])
def test_valid_reverse_numbering_is_preserved(order):
    with fitz.open() as document:
        page = columns(document, numbers=(10, 8, 9, 7))
        assert reasons(page, markdown_table(order)) == []


def test_only_one_inverted_lane_is_insufficient_for_this_multicolumn_guard():
    with fitz.open() as document:
        assert reasons(columns(document), markdown_table((2, 3, 1, 4))) == []


def test_real_authored_question_table_keeps_its_existing_native_boundary():
    with fitz.open() as document:
        page = columns(document, table=True)
        assert page.find_tables(use_layout=False).tables
        assert reasons(page, markdown_table()) == []


def test_data_table_does_not_turn_unrelated_question_order_into_table_evidence():
    with fitz.open() as document:
        page = columns(document)
        value = "|项目|数量|\n|---|---|\n|甲|3|\n\n" + "\n".join(prose(number) for number in (2, 4, 1, 3))
        assert reasons(page, value) == []


def test_without_a_markdown_table_the_new_guard_does_not_apply():
    with fitz.open() as document:
        assert reasons(columns(document), "\n".join(prose(number) for number in (2, 4, 1, 3))) == []


def test_code_fenced_table_literals_are_not_visible_question_table_evidence():
    with fitz.open() as document:
        assert reasons(columns(document), "```text\n" + markdown_table() + "\n```") == []


def test_missing_original_positions_are_not_invented():
    with fitz.open() as document:
        page = columns(document)
        class NoPositions:
            rect = page.rect
            rotation = 0
            def get_text(self, *_args, **_kwargs):
                return {"blocks": []}
        assert reasons(NoPositions(), markdown_table()) == []


def test_nonhorizontal_text_directions_do_not_establish_vertical_lane_order():
    with fitz.open() as document:
        page = columns(document)
        class SidewaysPositions:
            def __getattr__(self, name):
                return getattr(page, name)
            def get_text(self, kind="text", **kwargs):
                data = page.get_text(kind, **kwargs)
                if kind == "dict":
                    for block in data.get("blocks", []):
                        for line in block.get("lines", []):
                            line["dir"] = (0, -1)
                return data
        assert reasons(SidewaysPositions(), markdown_table()) == []


def test_ambiguous_duplicate_headings_do_not_establish_unique_order():
    with fitz.open() as document:
        assert reasons(columns(document, duplicate=True), markdown_table()) == []


def test_matching_question_number_without_the_original_prose_prefix_is_insufficient():
    with fitz.open() as document:
        value = markdown_table().replace(prose(1), "1. 另一个题目的未知条件")
        assert reasons(columns(document), value) == []


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_page_does_not_reuse_unrotated_lane_assumptions(rotation):
    with fitz.open() as document:
        assert reasons(columns(document, rotation=rotation), markdown_table()) == []


def test_bare_less_greater_relations_cannot_erase_an_intervening_question_heading():
    with fitz.open() as document:
        value = markdown_table().replace(prose(2), prose(2) + "x<3").replace(prose(3), prose(3) + "x>1")
        assert reasons(columns(document), value)


def test_trusted_inspector_page_routes_to_ocr_without_rewriting_its_original_markdown(monkeypatch, tmp_path):
    with fitz.open() as document:
        columns(document)
        source = document.tobytes()
    value = markdown_table()
    api = SimpleNamespace(
        extract_pages_markdown=lambda *_args, **_kwargs: SimpleNamespace(pages=[
            SimpleNamespace(page=0, markdown=value, needs_ocr=False, ocr_reason=None)]),
        extract_text_with_positions=lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(helper, "pdf_inspector", api)
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    actual_temp = helper.tempfile.NamedTemporaryFile
    monkeypatch.setattr(helper.tempfile, "NamedTemporaryFile", partial(actual_temp, dir=tmp_path))
    result = helper.inspect_and_extract_pdf(source)
    assert result["pages"][0]["needs_ocr"] is True
    assert result["pages"][0]["quality_reasons"]
    assert result["pages"][0]["markdown"] == value
    assert result["markdown"] is None
