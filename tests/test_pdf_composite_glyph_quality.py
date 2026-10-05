"""Composite TeX glyphs may be encoded correctly while math layout is lost."""

from types import SimpleNamespace
import os
import shutil
import subprocess

import pytest

from mathbank import pdf_inspector_helper as helper


@pytest.mark.parametrize("components", ["\uf8f1\uf8f2\uf8f3", "\uf8fc\uf8fd\uf8fe", "⎧⎨⎩", "⎫⎬⎭"])
def test_complete_brace_parts_require_layout_reconstruction_not_character_substitution(components):
    source = components[0] + "\n" + components[1] + " a+b，同奇偶；题目1：a*b=" + components[2] + " a×b，不同奇偶。"
    reasons = helper.native_text_quality_reasons(source)
    assert any("二维结构" in reason for reason in reasons)
    assert source.count(components[0]) == 1  # The quality probe does not rewrite evidence.


@pytest.mark.parametrize("source", [
    "1. 比较符号 ⎧ 的字形。\n2. 比较符号 ⎨ 的字形。\n3. 比较符号 ⎩ 的字形。",
    "题 1：比较符号 ⎫。\n题 2：比较符号 ⎬。\n题 3：比较符号 ⎭。",
    "字形 ⎧\n" + "普通说明文字" * 60 + "\n字形 ⎨\n" + "普通说明文字" * 60 + "\n字形 ⎩",
    "1. 代码示例 `⎧⎨⎩` 与 [链接](https://example.test/⎫⎬⎭)。",
])
def test_brace_inventory_across_questions_or_literals_is_not_a_broken_cases_formula(source):
    assert helper.native_text_quality_reasons(source) == []


@pytest.mark.parametrize("source", [
    "1. 函数 f(x)=⎧\n⎨ x+1，x>0；\n⎩ x-1，x≤0。",
    "1. 函数 f(x)=$⎧⎨ x+1，x>0；⎩ x-1，x≤0$。",
    "1. 已知 $x<y$，且 0 /∈ A、$y>0$。",
])
def test_local_cases_damage_and_detached_relations_stay_visible_in_math(source):
    assert helper.native_text_quality_reasons(source)


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


_NONLOCAL_BRACE = "⎧ ⎨ x+1，x>0\n3. Define f(x)=\n⎩ x-1，x≤0。"


def _brace_page(positions, headings=False):
    lines = []
    for index, (symbol, x, y) in enumerate(positions):
        chars = [{"c": symbol, "bbox": (x, y, x + 8, y + 12)}]
        if headings:
            chars = [{"c": char, "bbox": (10 + offset * 6, y, 16 + offset * 6, y + 12)}
                     for offset, char in enumerate(f"{index + 1}. Inspect ")] + chars
        lines.append({"dir": (1.0, 0.0), "spans": [{"size": 12, "chars": chars}]})
    return SimpleNamespace(rotation=0, get_text=lambda mode: {"blocks": [{"lines": lines}]})


def test_brace_components_crossing_extracted_question_number_are_checked_geometrically():
    page = _brace_page([(char, 100, y) for char, y in zip("⎧⎨⎩", (100, 111, 132))])
    assert not helper._has_local_brace_components(_NONLOCAL_BRACE)
    assert helper.native_text_quality_reasons(_NONLOCAL_BRACE) == []
    assert any("上下相邻" in reason for reason in helper._native_brace_geometry_reasons(_NONLOCAL_BRACE, page))


@pytest.mark.parametrize("positions,headings", [
    ([("⎧", 100, 100), ("⎨", 180, 111), ("⎩", 260, 132)], False),
    ([("⎧", 100, 100), ("⎨", 100, 200), ("⎩", 100, 300)], False),
    ([("⎧", 100, 100), ("⎨", 100, 114), ("⎩", 100, 128)], True),
])
def test_geometric_probe_does_not_join_separate_glyphs(positions, headings):
    assert helper._native_brace_geometry_reasons(_NONLOCAL_BRACE, _brace_page(positions, headings)) == []


@pytest.mark.parametrize("page", [None, SimpleNamespace(rotation=90),
    _brace_page([("⎧", 100, 100), ("⎨", 100, 111)]),
    _brace_page([("⎧", 100, 100), ("⎨", 100, 111), ("⎩", float("nan"), 132)]),
    SimpleNamespace(rotation=0, get_text=lambda mode: {}),
])
def test_missing_or_incomplete_brace_geometry_preserves_conservative_review(page):
    reasons = helper._native_brace_geometry_reasons(_NONLOCAL_BRACE, page)
    assert any("无法完整核对" in reason for reason in reasons)


def test_unreadable_pdf_retains_nonlocal_brace_review_without_table(monkeypatch):
    monkeypatch.setattr(helper, "_FITZ_AVAILABLE", False)
    assert helper._native_position_quality_reasons(_NONLOCAL_BRACE, 0, "missing.pdf")
    assert helper._native_brace_geometry_reasons("⎧⎨⎩ x+1") == []  # Local gate already rejects it.


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_MATH_NATIVE") != "1", reason="requires installed XeLaTeX and native PDF Inspector")
def test_real_pdf_independent_braces_pass_but_disordered_cases_fail_by_geometry(tmp_path):
    xelatex = shutil.which("xelatex")
    if not xelatex or not helper._PDF_INSPECTOR_AVAILABLE or not helper._FITZ_AVAILABLE:
        pytest.skip("native PDF dependencies unavailable")
    control = ("This is a native text extraction control. Every page uses the same installed fonts "
               "and contains enough ordinary ASCII prose to avoid a short text classification. "
               "Read every question in its original order and preserve all given values. "
               "The mathematical labels below are intentionally independent from this explanatory paragraph.")
    source = r"""\documentclass[12pt,fontset=fandol]{ctexart}
\usepackage[a4paper,margin=22mm]{geometry}
\usepackage{amsmath,amssymb,fontspec}
\setmainfont{texgyretermes-regular.otf}
\newfontfamily\symbolfont{XITSMath-Regular.otf}
\pagestyle{empty}\setlength{\parindent}{0pt}\setlength{\parskip}{16pt}
\begin{document}
""" + control + r"""
\par 1. Inspect the upper character {\symbolfont ⎧}. This is a standalone symbol.
\par 2. Inspect the middle character {\symbolfont ⎨}. This is a standalone symbol.
\par 3. Inspect the lower character {\symbolfont ⎩}. This is a standalone symbol.
\newpage
""" + control + r"""
\par 1. Given $x\neq y$, $x\notin A$ and $A\not\subset B$, inspect the relations.
\par 2. Calculate $\sqrt{2}$, $\frac{1}{2}$ and $x^2+y_1$.
\par 3. Define $f(x)=\begin{cases}x+1,&x>0,\\x-1,&x\leq0.\end{cases}$, then find $f(2)$.
\end{document}
"""
    tex = tmp_path / "brace-geometry.tex"
    tex.write_text(source)
    compiled = subprocess.run([xelatex, "-interaction=nonstopmode", "-halt-on-error",
                               f"-output-directory={tmp_path}", str(tex)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout[-2000:]
    pdf = tex.with_suffix(".pdf")
    extracted = helper.pdf_inspector.extract_pages_markdown(str(pdf))
    assert len(extracted.pages) == 2
    independent, cases = [page.markdown for page in extracted.pages]
    assert all(not page.needs_ocr for page in extracted.pages)
    assert all(char in independent for char in "⎧⎨⎩")
    with helper.fitz.open(pdf) as document:
        assert helper._native_brace_geometry_reasons(independent, document[0]) == []
        assert not helper._has_local_brace_components(cases)
        # This dedicated geometry assertion does not rely on the PUA gate.
        assert any("上下相邻" in reason for reason in helper._native_brace_geometry_reasons(cases, document[1]))
