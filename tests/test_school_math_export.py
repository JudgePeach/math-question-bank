"""School notation: real arcs, editable Word output, direct default PDF builds."""

import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
from lxml import etree

from mathbank.geometry_symbols import normalize_school_math_symbols
from mathbank.paper_helper import build_latex_document, clean_content_for_latex
from mathbank.word_export_helper import build_word_document


AUDIT = json.loads((Path(__file__).parent / "fixtures" / "school_math_symbols.json").read_text())
FONT_VARIANTS = {"blackboard_mathbbm", "blackboard_mathds"}
SUPPORTED = [item for item in AUDIT["samples"]
             if item["scope"] != "custom_shorthand" and item["id"] not in FONT_VARIANTS]
ALTERNATIVES = [item for item in AUDIT["samples"] if item not in SUPPORTED]
ARC_SAMPLES = [
    r"\wideparen{AB}", r"\wideparen{ABC}", r"\wideparen{ABCDEFGH}",
    r"\wideparen{A_1B_2C_3}", r"\wideparen{\overrightarrow{AB}}",
    r"\wideparen{\wideparen{AB}C}", r"\wideparen{AB}_{\wideparen{CD}}",
    r"\overparen{ABC}", r"\widearc{ABC}", r"\overarc{ABC}", r"\overarc[1]{ABC}",
]
UNIT_SAMPLES = [
    r"\ang{30}", r"\ang{-30.5}", r"\ang{30;15;20}",
    r"20\celsius", "20℃", r"\text{温度20℃}", r"\text{温度20\celsius}",
    r"5\perthousand", r"5\textperthousand", r"5\permil", "5‰",
    r"\text{5\textperthousand}", r"\text{5‰}",
    r"\sfrac{1}{2}", r"\sfrac{x+1}{y-1}", r"\sfrac{\sfrac{1}{2}}{3}",
]
BARE_CONTENTS = [
    r"圆弧\wideparen{AB}，\widearc{ABC}，\overparen{ABCD}，\overarc[1]{AB}。",
    r"角度\ang{30;15;20}，\ang {30;15;20}；分数\sfrac{1}{2}。",
    r"$\text{圆弧\wideparen{AB}与\overparen{ABC}}$。",
    r"\text{圆弧\wideparen{AB}，角度\ang{30}，分数\sfrac{1}{2}}。",
    r"气温20℃，误差5‰；\celsius$x$，\perthousand$x$。",
]


def _questions(formulas):
    return [{"question": {"id": index + 1, "question_type": "detailed_answer",
                          "content": "$" + formula + "$"}, "score": 0}
            for index, formula in enumerate(formulas)]


def _tex(formulas):
    return build_latex_document("数学符号验证", "", "quiz", _questions(formulas),
                                show_secret=False, show_notice=False)


def test_default_preamble_has_arc_aliases_and_units_without_yhmath():
    tex = _tex(ARC_SAMPLES + UNIT_SAMPLES)
    assert r"\providecommand{\wideparen}[1]{\overparen{#1}}" in tex
    assert r"\providecommand{\widearc}[1]{\overparen{#1}}" in tex
    assert r"\ProvideDocumentCommand{\overarc}{O{1}m}" in tex
    assert r"\providecommand{\degree}" in tex
    assert r"\providecommand{\celsius}" in tex
    assert r"\providecommand{\perthousand}" in tex
    assert r"\usepackage{xfrac}" in tex
    assert r"\usepackage{xfrac}" not in _tex(ARC_SAMPLES)
    assert "yhmath" not in tex
    assert "℃" not in tex


@pytest.mark.parametrize("source", ARC_SAMPLES + UNIT_SAMPLES)
def test_word_normalization_is_idempotent_and_preserves_formula_delimiters(source):
    content = "已知 $" + source + "$。"
    result = normalize_school_math_symbols(content)
    assert normalize_school_math_symbols(result) == result
    assert result.startswith("已知 $") and result.endswith("$。")
    assert r"\widehat" not in result and r"\overset{\frown}" not in result


@pytest.mark.parametrize("source", [
    r"`\widearc{AB} \celsius ℃ ‰`",
    "```latex\n\\widearc{AB} ℃ ‰\n```\n",
    r"![℃‰](/static/uploads/℃‰.png)",
    r"\includegraphics{℃‰.png}",
    r"\begin{tikzpicture}\node {$\widearc{AB}$ ℃ ‰};\end{tikzpicture}",
    r'<mathbank-math id="MBM_ARC_0001">$\widearc{AB}$ ℃ ‰</mathbank-math>',
    r"\widearcCustom{AB}", r"\\widearc{AB}",
    r"\mathbbm{R}", r"\mathds{R}", r"\abs{x}", r"\norm{x}", r"\Var(X)", r"\Cov(X,Y)",
    r"\overarc[2]{AB}", r"\ang[angle-symbol-degree=\ast]{30}",
])
@pytest.mark.parametrize("target", ["word", "latex"])
def test_protected_or_unimplemented_notation_is_not_silently_rewritten(source, target):
    assert normalize_school_math_symbols(source, target=target) == source


def test_units_in_prose_and_adjacent_math_do_not_form_double_dollars():
    source = r"气温20℃，误差为5‰。\celsius$x$，\perthousand$x$，\widearc{AB}$=1$。"
    word = normalize_school_math_symbols(source)
    assert "$$" not in word
    assert "℃" not in word
    latex = clean_content_for_latex(source)
    assert "℃" not in latex and "‰" not in latex
    assert "$$" not in latex


@pytest.mark.parametrize("content", BARE_CONTENTS)
def test_bare_shortcut_and_mixed_text_word_normalization_is_idempotent(content):
    result = normalize_school_math_symbols(content)
    assert normalize_school_math_symbols(result) == result
    assert "$$" not in result


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Native Pandoc required")
def test_native_word_arcs_units_and_slanted_fractions_are_editable():
    questions = _questions(ARC_SAMPLES + UNIT_SAMPLES)
    original = copy.deepcopy(questions)
    data, diagnostics = build_word_document("符号验证", "", "quiz", questions,
                                            show_secret=False, show_notice=False)
    assert questions == original
    assert diagnostics["native_formulas"] == len(questions)
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        assert not any(name.startswith("word/media/") for name in archive.namelist())
    ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
    arcs = root.xpath(".//m:groupChr/m:groupChrPr/m:chr/@m:val", namespaces=ns)
    assert len(arcs) == len(ARC_SAMPLES) + 2  # Two nested arcs.
    assert set(arcs) == {"⏜"}  # U+23DC top parenthesis, never a circumflex.
    assert root.xpath(".//m:groupChr/m:e/m:acc", namespaces=ns)  # Arc over vector.
    assert root.xpath(".//m:groupChr/m:e/m:groupChr", namespaces=ns)
    assert root.xpath(".//m:sSup", namespaces=ns) and root.xpath(".//m:sSub", namespaces=ns)
    assert not root.xpath(".//m:fPr/m:type[@m:val='bar']", namespaces=ns)
    assert "‰" in "".join(root.xpath(".//m:t/text()", namespaces=ns))


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Native Pandoc required")
def test_native_word_bare_shortcuts_and_text_groups_are_not_plain_latex():
    questions = [{"question": {"id": index + 1, "question_type": "detailed_answer",
                              "content": source}, "score": 0}
                 for index, source in enumerate(BARE_CONTENTS)]
    data, diagnostics = build_word_document("裸符号验证", "", "quiz", questions,
                                            show_secret=False, show_notice=False)
    assert diagnostics["native_formulas"] >= 10
    assert diagnostics["fallback_formulas"] == diagnostics["failed_formulas"] == 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
          "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    plain = "".join(root.xpath(".//w:t/text()", namespaces=ns))
    assert not any(command in plain for command in (r"\wideparen", r"\overparen", r"\widearc", r"\ang", r"\sfrac"))
    assert len(root.xpath(".//m:groupChr/m:groupChrPr/m:chr[@m:val='⏜']", namespaces=ns)) == 7
    math_text = "".join(root.xpath(".//m:t/text()", namespaces=ns))
    assert "′" in math_text and "″" in math_text


NATIVE = pytest.mark.skipif(
    os.environ.get("MATHBANK_TEST_SYMBOL_NATIVE") != "1" or not shutil.which("xelatex"),
    reason="Opt-in native default-template XeLaTeX validation",
)


def _compile_direct(tex, directory, *, succeeds=True):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "paper.tex").write_text(tex)
    command = [shutil.which("xelatex"), "-no-shell-escape", "-halt-on-error",
               "-interaction=nonstopmode", "paper.tex"]
    log = ""
    for _ in range(2 if succeeds else 1):
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=45)
        log += result.stdout + result.stderr
        if succeeds:
            assert result.returncode == 0, log[-7000:]
        else:
            assert result.returncode != 0
    if succeeds:
        assert "Missing character" not in log
        assert "Undefined control sequence" not in log
        assert "invalid in math mode" not in log
        import pymupdf as fitz
        with fitz.open(directory / "paper.pdf") as document:
            for index, page in enumerate(document):
                assert not page.get_images(full=True)
                page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(directory / f"page-{index + 1}.png")
    return log


@NATIVE
def test_native_pdf_audit_all_supported_and_explicit_alternatives(tmp_path):
    assert len(SUPPORTED) == 114 and len(ALTERNATIVES) == 6
    _compile_direct(_tex([item["latex"] for item in SUPPORTED]), tmp_path / "supported-114")
    # These six original custom/font commands remain unsupported; the test
    # intentionally compiles only the explicitly documented standard forms.
    _compile_direct(_tex([item["alternative"] for item in ALTERNATIVES]), tmp_path / "alternatives-6")


@NATIVE
def test_native_pdf_arc_shapes_nested_scripts_vectors_and_units(tmp_path):
    _compile_direct(_tex(ARC_SAMPLES + UNIT_SAMPLES), tmp_path / "arc-and-unit-shapes")
    questions = [{"question": {"id": index + 1, "question_type": "detailed_answer",
                              "content": source}, "score": 0}
                 for index, source in enumerate(BARE_CONTENTS)]
    tex = build_latex_document("裸符号与正文验证", "", "quiz", questions,
                                show_secret=False, show_notice=False)
    _compile_direct(tex, tmp_path / "bare-and-text-symbols")


@NATIVE
def test_native_pdf_rejects_unsupported_optional_arc_width(tmp_path):
    log = _compile_direct(_tex([r"\overarc[2]{AB}"]), tmp_path / "unsupported-width", succeeds=False)
    assert "Unsupported optional overarc width" in log
