"""Inline image geometry must be checked against the extracted content slot."""

from types import SimpleNamespace

import pytest

from mathbank import pdf_inspector_helper as helper


def _items(count=3, *, repeated_anchors=False):
    items = []
    for index in range(count):
        number = 1 if repeated_anchors else index + 1
        y = 80 + index * 40
        items.extend([
            SimpleNamespace(item_type="text", x=30, y=y, width=70, height=10,
                            text=f"{number}. 已知函数"),
            SimpleNamespace(item_type="image", x=104, y=y - 1, width=28, height=12),
            SimpleNamespace(item_type="text", x=136, y=y, width=110, height=10,
                            text="，求其最小值。"),
        ])
    return items


def _markdown(expressions, *, repeated_anchors=False):
    return "\n".join(
        f"{1 if repeated_anchors else index + 1}. 已知函数 {expression} ，求其最小值。"
        for index, expression in enumerate(expressions)
    )


@pytest.mark.parametrize("expression", [
    "$f(x)=x^2$",
    "$$f(x)=x^2$$",
    r"\(f(x)=x^2\)",
    r"\[f(x)=x^2\]",
    r"\begin{equation}f(x)=x^2\end{equation}",
    r"$\dfrac{x+1}{x-1}$",
    "![公式](formula.png)",
    '<img src="formula.png" alt="公式" />',
])
def test_inline_images_do_not_force_ocr_when_original_slots_have_content(expression):
    assert not helper._has_multiple_inline_formula_images(
        _items(), markdown=_markdown([expression] * 3),
    )


def test_retained_slots_may_mix_formula_and_image_formats():
    assert not helper._has_multiple_inline_formula_images(
        _items(), markdown=_markdown(["$x^2$", "![公式](b.png)", r"\(x^3\)"]),
    )


@pytest.mark.parametrize("missing", [
    "", "$ $", "$$ $$", r"\(\quad\)", "$?$", r"$\text{?}$", r"$\frac{}{}$", "$\ufffd$", "$\ue123$",
    "$unknown$", "$公式待识别$", "![公式待补](missing.png)", "$x^2",
])
def test_one_unresolved_slot_preserves_formula_loss_fallback(missing):
    assert helper._has_multiple_inline_formula_images(
        _items(), markdown=_markdown(["$x^2$", missing, "$x^4$"]),
    )


def test_formulas_elsewhere_do_not_cover_missing_original_slots():
    markdown = _markdown(["", "", ""]) + "\n附录：$x^2$ $x^3$ $x^4$"
    assert helper._has_multiple_inline_formula_images(_items(), markdown=markdown)


def test_repeated_anchor_pairs_remain_unresolved_instead_of_count_based_acceptance():
    assert helper._has_multiple_inline_formula_images(
        _items(repeated_anchors=True),
        markdown=_markdown(["$x^2$", "$x^3$", "$x^4$"], repeated_anchors=True),
    )


def test_one_formula_slot_cannot_cover_three_distinct_source_regions():
    assert helper._has_multiple_inline_formula_images(
        _items(repeated_anchors=True), markdown=_markdown(["$x^2$"]),
    )


def test_retained_inline_formulas_are_passed_to_position_check_with_original_page(monkeypatch):
    calls = []

    def extract(path, pages):
        calls.append((path, pages))
        return _items()

    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", SimpleNamespace(extract_text_with_positions=extract))
    assert not helper._has_math_formula_loss(
        _markdown(["$x^2$", "$x^3$", "$x^4$"]), page_index=4, pdf_path="synthetic.pdf",
    )
    assert calls == [("synthetic.pdf", [5])]


def test_real_formula_loss_still_falls_back_with_retained_formulas_elsewhere(monkeypatch):
    monkeypatch.setattr(helper, "_PDF_INSPECTOR_AVAILABLE", True)
    monkeypatch.setattr(helper, "pdf_inspector", SimpleNamespace(extract_text_with_positions=lambda *a, **k: _items()))
    markdown = _markdown(["$x^2$", "$x^3$", "$x^4$"]) + "\n4. A. B. C. D."
    assert helper._has_math_formula_loss(markdown, pdf_path="synthetic.pdf")
