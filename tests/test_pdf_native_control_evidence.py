"""Control-character evidence rejects native certification without rewriting."""
import pytest
from types import SimpleNamespace

from mathbank.pdf_inspector_helper import native_text_quality_reasons


@pytest.mark.parametrize("code", [*range(9), 11, 12, *range(14, 32), *range(127, 160)])
def test_non_layout_control_is_not_certified_as_complete_native_math(code):
    source = "1. 已知 $x=" + chr(code) + "1$，求 $x+1$。"
    unchanged = source.encode()
    reasons = native_text_quality_reasons(source)
    assert any("非排版控制字符" in reason for reason in reasons)
    assert all(chr(code) not in reason for reason in reasons)
    assert source.encode() == unchanged


@pytest.mark.parametrize("separator", ["\t", "\n", "\r", "\r\n", " ", "\u00a0"])
def test_legitimate_layout_whitespace_stays_native(separator):
    assert native_text_quality_reasons("1. 已知 $x=1$，" + separator + "求 $x+1$。") == []


def test_visible_code_escape_is_not_an_actual_control_character():
    assert native_text_quality_reasons(r"1. 代码中 `\x00` 是一种转义写法，求 $x+1$。") == []


def test_actual_page_result_retains_control_evidence_and_uses_existing_ocr_route(monkeypatch):
    from mathbank import pdf_inspector_helper as helper
    clean = "1. 已知 $x=1$，求 $x+1$。"
    damaged = "2. 已知 $x=\x071$，求 $x+2$。"
    inspector = SimpleNamespace(extract_pages_markdown=lambda *_a, **_k: SimpleNamespace(pages=[
        SimpleNamespace(page=0, markdown=clean, needs_ocr=False),
        SimpleNamespace(page=1, markdown=damaged, needs_ocr=False),
    ]))
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", inspector)
    result = helper.inspect_and_extract_pdf(b"isolated-native-fault-probe")
    assert result["pages_needing_ocr"] == [1]
    assert result["pages"][1]["markdown"] == damaged
    assert damaged not in result["markdown"]
    assert clean in result["markdown"]
