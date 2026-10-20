"""Word-only exam-zh constants; real Pandoc, no API/main/database imports."""

import copy
import io
import subprocess
import zipfile

import pytest
from lxml import etree

from mathbank import word_export_helper as word


NS = {"w": word.WORD_NS, "m": word.MATH_NS}
MVAL = "{" + word.MATH_NS + "}val"


@pytest.mark.parametrize("source, expected", [
    (r"\iu+\upi+\eu+\upe+i+e", r"{\mathrm{i}}+{\mathrm{i}}+{\mathrm{e}}+{\mathrm{e}}+i+e"),
    (r"\frac\iu\eu+x^\upi+y_\upe", r"\frac{\mathrm{i}}{\mathrm{e}}+x^{\mathrm{i}}+y_{\mathrm{e}}"),
    (r"\iu_2+\eu^{i}+\upi{}+\upe2", r"{\mathrm{i}}_2+{\mathrm{e}}^{i}+{\mathrm{i}}{}+{\mathrm{e}}2"),
    (r"\du+\uppi+\iux+\upix+\europa+\upefoo+\Iu+\iu@private+i+e", None),
    (r"\\iu+\\eu+\\upi+\\upe", None),
    (r"\\\iu", r"\\{\mathrm{i}}"),
    (r"\%+\iu", r"\%+{\mathrm{i}}"),
])
def test_only_complete_known_unescaped_atoms_are_prepared(source, expected):
    expected = source if expected is None else expected
    assert word._prepare_word_formula(source) == expected
    assert word._prepare_word_formula(expected) == expected


@pytest.mark.parametrize("literal", [
    r"`\iu`", "```tex\n\\iu\n```", r"\verb|\iu|", r"\verb*|\iu|",
    r"\Verb|\iu|", r"\lstinline[language=tex]|\iu|",
    r"\text{\string\iu}", r"\detokenize{\iu}", r"\unexpanded{\iu}", r"\texttt{\iu}", r"\noexpand\iu",
    r"\url{https://example.test/\iu}", r"\href{https://example.test/\iu}{link}",
    r"https://example.test/\iu", r"/static/uploads/\iu.png",
    r"\includegraphics{\iu.png}", r"\path{\iu}", r"\label{\iu}",
    r"<code>\iu</code>", r"\begin{verbatim}\iu\end{verbatim}",
    r"\begin{tikzpicture}\iu\end{tikzpicture}",
    r"\newcommand{\iu}{j}", r"\renewcommand{\iu}{j}", r"\def\iu{j}",
    r"\catcode`\@=11", r"\csname iu\endcsname", "% literal \\iu\n",
    r"\makeatletter", r"\makeatother", r"\DeclareMathOperator{\myop}{f}",
    r"\newenvironment{x}{}{}", r"\renewenvironment{x}{}{}",
    r"\RenewDocumentCommand{\iu}{}{j}",
])
def test_literal_or_uncertain_formula_stays_exact(literal):
    source = literal + r"+\eu"
    assert word._prepare_word_formula(source) == source


@pytest.fixture
def native_converter():
    path = word._find_pandoc()
    if not path:
        pytest.skip("Real Pandoc is required for native Word acceptance")
    return word.PandocOmmlConverter(path)


def _styled_letter_runs(xml):
    root = word.parse_xml(xml)
    word._set_native_omml_run_styles(root)
    result = []
    for run in root.iter("{" + word.MATH_NS + "}r"):
        text = "".join(node.text or "" for node in run.findall("m:t", NS))
        if text not in {"i", "e"}:
            continue
        style = run.find("m:rPr/m:sty", NS).get(MVAL)
        if style == "i":
            assert run.find("w:rPr/w:rFonts", NS).get("{" + word.WORD_NS + "}ascii") == word.LATIN_FONT
            assert run.find("w:rPr/w:i", NS) is not None
        else:
            assert style == "p"
            assert run.find("w:rPr/w:i", NS) is None
        result.append((text, style))
    return result


@pytest.mark.parametrize("formula, expected", [
    (r"\iu+i+\upi+i+\eu+e+\upe+e", [("i", "p"), ("i", "i"), ("i", "p"), ("i", "i"),
                                                   ("e", "p"), ("e", "i"), ("e", "p"), ("e", "i")]),
    (r"\frac{\iu}{i}+\frac{\eu}{e}+x_{\upi}^{\upe}+e^{\iu}",
     [("i", "p"), ("i", "i"), ("e", "p"), ("e", "i"), ("i", "p"), ("e", "p"), ("e", "i"), ("i", "p")]),
    (r"\frac\iu\eu+x^\upi+y_\upe", [("i", "p"), ("e", "p"), ("i", "p"), ("e", "p")]),
])
def test_real_pandoc_preserves_constant_and_variable_styles(native_converter, formula, expected):
    result = native_converter.convert_many([formula])
    assert list(result) == [formula], "Original formula is the map key, not the prepared copy"
    assert _styled_letter_runs(result[formula]) == expected


def _export(question, tmp_path, monkeypatch):
    def no_fallback(formula):
        pytest.fail("Known constant formulas must convert natively: " + formula)
    monkeypatch.setattr(word, "_formula_fallback_png", no_fallback)
    questions = [{"question": question, "score": 5}]
    original = copy.deepcopy(questions)
    data, diagnostics = word.build_word_document(
        "Word constants", "", "exam", questions, include_answers=True,
        show_secret=False, show_notice=False, uploads_dir=tmp_path,
    )
    assert questions == original
    assert diagnostics["failed_formulas"] == diagnostics["fallback_formulas"] == 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    assert "[公式待核对：" not in "".join(root.itertext())
    return root, diagnostics


def test_15726_stem_is_native_without_rewriting_source(native_converter, tmp_path, monkeypatch):
    question = {"id": 15726, "question_type": "fill_in_blank",
                "content": r"已知复数 \((a+2\iu)(1+\iu)\) 的实部为 \(0\)，其中 \(\iu\) 为虚数单位，则实数 \(a\) 的值是\fillin.",
                "answer_markdown": r"【答案】\(2\)", "source": "2019年江苏卷 第2题"}
    root, diagnostics = _export(question, tmp_path, monkeypatch)
    assert diagnostics["native_formulas"] == 5
    assert len(root.xpath(".//m:oMath", namespaces=NS)) == 5
    assert "2" in root.xpath(".//m:t/text()", namespaces=NS)


@pytest.mark.parametrize("table", [
    "| 量 | 值 |\n| --- | --- |\n| $\\eu$ | $e^{\\iu}$ |",
    r"\begin{tabular}{cc}量 & 值 \\ $\eu$ & $e^{\iu}$ \\ \end{tabular}",
])
def test_constants_in_stem_choices_tables_and_answer(native_converter, tmp_path, monkeypatch, table):
    question = {"id": 1, "question_type": "single_choice",
                "content": r"求 $\frac{\iu}{i}+\eu^{e}$。" + "\n\n" + table + "\n\n" +
                           r"\begin{choices}\item $\iu$\item $\upi$\item $\eu$\item $\upe$\end{choices}",
                "answer_markdown": r"解析 $\upe^{\upi}+e_i$。" + "\n\n" + table}
    root, diagnostics = _export(question, tmp_path, monkeypatch)
    assert diagnostics["native_formulas"] >= 8
    assert root.xpath(".//w:tbl", namespaces=NS), "The table remains an editable Word table"
    styles = _styled_letter_runs(etree.tostring(root))
    assert ("i", "p") in styles and ("i", "i") in styles
    assert ("e", "p") in styles and ("e", "i") in styles


def test_png_fallback_uses_same_preparation_and_original_diagnostic_key(monkeypatch):
    captured = []
    monkeypatch.setattr(word.shutil, "which", lambda name: "/isolated/xelatex")
    def run(args, **kwargs):
        captured.append((kwargs["cwd"] / "formula.tex").read_text())
        return subprocess.CompletedProcess(args, 1, b"", b"")
    monkeypatch.setattr(word.subprocess, "run", run)
    source = r"\frac\iu\eu+i+e+\du"
    assert word._formula_fallback_png(source) is None
    assert word._prepare_word_formula(source) in captured[0]
    assert r"\du" in captured[0]
    assert source == r"\frac\iu\eu+i+e+\du"


def test_unconvertible_formula_diagnostic_keeps_the_authored_alias(monkeypatch, tmp_path):
    source = r"\iu+\du"
    seen = []
    monkeypatch.setattr(word.PandocOmmlConverter, "convert_many", lambda self, formulas: {})
    monkeypatch.setattr(word, "_formula_fallback_png", lambda formula: seen.append(formula))
    data, diagnostics = word.build_word_document(
        "Original formula diagnostic", "", "exam",
        [{"question": {"content": "$" + source + "$", "question_type": "fill_in_blank"}, "score": 5}],
        uploads_dir=tmp_path, show_notice=False, show_secret=False,
    )
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        text = "".join(etree.fromstring(z.read("word/document.xml")).itertext())
    assert seen == [source]
    assert diagnostics["failed_formulas"] == 1
    assert "[公式待核对：" + source + "]" in text
    assert r"\mathrm" not in text
