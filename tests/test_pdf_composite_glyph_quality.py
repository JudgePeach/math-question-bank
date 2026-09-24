"""Composite TeX glyphs may be encoded correctly while math layout is lost."""

from types import SimpleNamespace

import pytest

from mathbank import pdf_inspector_helper as helper


@pytest.mark.parametrize("components", ["\uf8f1\uf8f2\uf8f3", "\uf8fc\uf8fd\uf8fe", "⎧⎨⎩", "⎫⎬⎭"])
def test_complete_brace_parts_require_layout_reconstruction_not_character_substitution(components):
    source = components[0] + "\n" + components[1] + " a+b，同奇偶；题目1：a*b=" + components[2] + " a×b，不同奇偶。"
    reasons = helper.native_text_quality_reasons(source)
    assert any("二维结构" in reason for reason in reasons)
    assert source.count(components[0]) == 1  # The quality probe does not rewrite evidence.


@pytest.mark.parametrize("source", [
    "A. 0 /*∈ A*", "A. 0 / *∈ A*", "集合 {x|x=4k+2} ̸⊂ M",
    "集合 A /⊆ B", "\u0338⊂ M",
])
def test_detached_negation_is_not_accepted_as_a_reliable_native_relation(source):
    assert any("否定斜线" in reason for reason in helper.native_text_quality_reasons(source))


@pytest.mark.parametrize("source", [
    "A. $0\\notin A$", "集合 $A\\not\\subset M$", "A. 0 ∉ A，集合 A ⊄ B。",
    "集合 A ⊂\u0338 B，原符号已按组合顺序书写。",
    "Latin cafe\u0301, a\u0308, n\u0303 and o\u0338 remain unchanged.",
    "变量 o\u0338∈A，a\u0301∈B 是正常组合重音。", "普通字符串 / 不代表否定。",
    "参见 https://example.test/∈ 与 `0 /∈ A` 的字符示例。",
])
def test_normal_relations_and_latin_combining_accents_remain_native(source):
    assert helper.native_text_quality_reasons(source) == []


def test_latex_provenance_does_not_certify_scrambled_cases_or_detached_negation(monkeypatch):
    original = "\uf8f1 \uf8f2 *a*+*b*, 同奇偶，题目 **1**：规定a*b=\uf8f3 a×b，不同奇偶。\nA. 0 /*∈ A*"
    inspector = SimpleNamespace(extract_pages_markdown=lambda *a, **kw: SimpleNamespace(pages=[
        SimpleNamespace(page=0, markdown=original, needs_ocr=False, producer="XeTeX")]))
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", inspector)
    result = helper.inspect_and_extract_pdf(b"non-pdf mock")
    page = result["pages"][0]
    assert page["markdown"] == original and page["needs_ocr"] is True
    assert result["pages_needing_ocr"] == [0] and result["markdown"] is None
    assert any("二维结构" in reason for reason in page["quality_reasons"])
    assert any("否定斜线" in reason for reason in page["quality_reasons"])
