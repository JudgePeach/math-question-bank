import pytest

from mathbank.fraction_style import normalize_fraction_style


@pytest.mark.parametrize("opening,closing", [("$", "$"), ("$$", "$$"), (r"\(", r"\)"), (r"\[", r"\]")])
def test_main_fraction_and_script_fraction_in_all_delimiters(opening, closing):
    source = opening + r"\frac{x+1}{x-1}+2^{\dfrac{n+1}{2}}+a_{\dfrac12}" + closing
    expected = opening + r"\dfrac{x+1}{x-1}+2^{\frac{n+1}{2}}+a_{\frac12}" + closing
    assert normalize_fraction_style(source) == expected
    assert normalize_fraction_style(expected) == expected


def test_nested_fractions_and_unbraced_script_arguments_keep_scope():
    source = r"$\frac{1+\dfrac1x}{\dfrac23}+x^\dfrac12+\frac34+a_\dfrac12$"
    expected = r"$\dfrac{1+\frac1x}{\frac23}+x^\frac12+\dfrac34+a_\frac12$"
    assert normalize_fraction_style(source) == expected


@pytest.mark.parametrize("constant", ["eu", "upe", "iu", "upi"])
@pytest.mark.parametrize("opening,closing", [("$", "$"), ("$$", "$$"), (r"\(", r"\)"), (r"\[", r"\]")])
def test_examzh_constants_do_not_block_fraction_normalization(constant, opening, closing):
    atom = "\\" + constant
    source = opening + atom + r"^x-\frac{2}{3}\sin x" + closing
    expected = source.replace(r"\frac", r"\dfrac")
    assert normalize_fraction_style(source) == expected
    assert normalize_fraction_style(expected) == expected


@pytest.mark.parametrize("constant", ["eu", "upe", "iu", "upi"])
def test_examzh_constants_are_atoms_and_keep_compact_fraction_contexts(constant):
    atom = "\\" + constant
    source = "$" + r"\frac" + atom + r"{3}+\frac{1+" + atom + r"^{\dfrac12}}{\dfrac" + atom + r"{2}}+x^" + atom + r"{\frac34}$"
    expected = "$" + r"\dfrac" + atom + r"{3}+\dfrac{1+" + atom + r"^{\frac12}}{\frac" + atom + r"{2}}+x^" + atom + r"{\dfrac34}$"
    assert normalize_fraction_style(source) == expected
    assert normalize_fraction_style(expected) == expected


@pytest.mark.parametrize("command", ["du", "eux", "upex", "iux", "upix"])
def test_unknown_constant_commands_still_preserve_entire_formula(command):
    source = r"$\frac12+" + "\\" + command + r"^{\dfrac34}$"
    assert normalize_fraction_style(source) == source


def test_examzh_constants_in_protected_sources_are_preserved():
    protected = (
        r"`$\eu+\frac12$` "
        r"\begin{tikzpicture}\node {$\iu+\frac12$};\end{tikzpicture} "
        r"\text{示例 $\upe+\frac12$} "
        '<mathbank-math id="MBM_DOCX_0001">$\\upi+\\frac12$</mathbank-math>'
    )
    assert normalize_fraction_style(protected + r" $\eu+\frac12$") == protected + r" $\eu+\dfrac12$"


def test_explicit_small_and_continued_fractions_are_retained():
    source = r"$\tfrac{\dfrac12}{3}+\cfrac[l]{1}{2+\dfrac34}$"
    expected = r"$\tfrac{\frac12}{3}+\cfrac[l]{1}{2+\frac34}$"
    assert normalize_fraction_style(source) == expected


def test_sqrt_index_is_small_and_radicand_follows_its_parent():
    source = r"$\sqrt[\dfrac12]{\frac34}+x^{\sqrt{\dfrac56}}+\frac{\sqrt{\dfrac12}}{2}$"
    expected = r"$\sqrt[\frac12]{\dfrac34}+x^{\sqrt{\frac56}}+\dfrac{\sqrt{\frac12}}{2}$"
    assert normalize_fraction_style(source) == expected


@pytest.mark.parametrize("environment", ["equation", "equation*", "align", "align*", "alignat", "alignat*", "gather", "gather*", "multline", "multline*", "displaymath", "math"])
def test_standalone_environments_and_nested_cases(environment):
    body = r"\begin{cases}\frac12 & x>0\\x^{\dfrac12} & x\leq0\end{cases}"
    source = rf"\begin{{{environment}}}" + body + rf"\end{{{environment}}}"
    expected = source.replace(r"\frac12", r"\dfrac12").replace(r"x^{\dfrac12}", r"x^{\frac12}")
    assert normalize_fraction_style(source) == expected


@pytest.mark.parametrize("protected", [
    r"普通文本中的 \frac{a}{b} 和 \dfrac{a}{b}",
    r"`$\frac12$`",
    "```latex\n$\\frac12$\n```",
    "~~~latex\n$\\frac12$\n~~~",
    r"\begin{tikzpicture}\node {$\frac12$};\end{tikzpicture}",
    r"\begin{axis}\node {$\frac12$};\end{axis}",
    r"\begin{verbatim}$\frac12$\end{verbatim}",
    '<mathbank-math id="MBM_DOCX_0001">$\\frac12$</mathbank-math>',
    r"[[MBM_DOCX_0001]]",
    r"![图 $\frac12$](/static/uploads/a.png)",
    r"\text{使用 $\frac12$ 作为示例}",
    r"\verb|$\frac12$|",
])
def test_nonmath_code_tikz_locks_and_text_examples_are_unchanged(protected):
    assert normalize_fraction_style(protected + "\n" + r"另有 $\frac34$") == protected + "\n" + r"另有 $\dfrac34$"


def test_comments_and_escaped_commands_do_not_confuse_braces_or_delimiters():
    source = "$\\frac{1% } $\\dfrac34\n+2}{3}+\\text{示例 \\frac12}+\\\\frac12$"
    expected = source.replace("$\\frac{1", "$\\dfrac{1", 1)
    assert normalize_fraction_style(source) == expected
    assert normalize_fraction_style(r"价格 \$5，$\frac12+\text{\$5}$") == r"价格 \$5，$\dfrac12+\text{\$5}$"


@pytest.mark.parametrize("source", [
    r"$\frac{1}{2$", r"$\frac12}$", r"$\frac{1}$", r"$x^}\frac12$",
    r"$\frac12", r"\[\frac12", r"$\sqrt[3{\frac12}$",
    r"$\begin{cases}\frac12\end{aligned}$",
    r"$\displaystyle\frac12$", r"$\genfrac{}{}{}{}{\frac12}{3}$",
    r"$\overset{\frac12}{x}$", r"$\newcommand{\foo}{\frac12}$",
    r"$\frac\custom{x}{\frac12}$",
    r"$\foo{\frac12}+\frac34$", r"$\foo\frac12$",
    r"$\frac12+\left(\frac34$", r"$\right)\frac12$",
    r"$\frac12+\begin{smallmatrix}\frac34\end{smallmatrix}$",
])
def test_uncertain_or_incomplete_math_is_left_unchanged(source):
    assert normalize_fraction_style(source) == source


def test_does_not_touch_other_math_commands_or_bytes():
    source = r"题目 $\frac {\mathbf{a}+\vec b}{2}\leq x$。\begin{choices}\item $\frac12$\end{choices}"
    assert normalize_fraction_style(source) == source.replace(r"\frac", r"\dfrac")


def test_table_cells_only_normalize_delimited_math():
    source = r"\begin{tabular}{cc}$\frac12$ & 文本 \frac34\end{tabular}"
    assert normalize_fraction_style(source) == source.replace(r"$\frac12$", r"$\dfrac12$")


def test_recursion_guard_leaves_pathological_input_unchanged():
    source = "$" + "{" * 150 + r"\frac12" + "}" * 150 + "$"
    assert normalize_fraction_style(source) == source


def test_comments_between_fraction_arguments_are_whitespace():
    source = "$\\frac% numerator\n\\dfrac12% denominator\n\\dfrac34+\\frac56$"
    expected = "$\\dfrac% numerator\n\\frac12% denominator\n\\frac34+\\dfrac56$"
    assert normalize_fraction_style(source) == expected


def test_common_math_commands_and_delimiters_do_not_prevent_conversion():
    source = r"$\forall x\in\mathbb{R},\quad\left\{\frac{\sin x}{\pi}\right\}\leqslant\frac12$"
    assert normalize_fraction_style(source) == source.replace(r"\frac", r"\dfrac")


def test_original_private_unicode_characters_do_not_collide_with_placeholders():
    source = "\U000f0000 `code` " + r"$\frac12$"
    assert normalize_fraction_style(source) == source.replace(r"\frac", r"\dfrac")


@pytest.mark.parametrize("protected", [
    r"`` $\frac12$ `x` ``",
    r"![alt [inner $\frac12$] outer](/static/uploads/a.png)",
    r"![alt \] [inner $\frac12$] outer](/static/uploads/a(1).png)",
])
def test_nested_code_and_image_alt_text_are_protected(protected):
    assert normalize_fraction_style(protected + r" $\frac34$") == protected + r" $\dfrac34$"


@pytest.mark.parametrize("source", [r"`` $\frac12$ `x`", r"![alt [inner $\frac12$]"])
def test_incomplete_code_and_images_are_left_unchanged(source):
    assert normalize_fraction_style(source) == source
