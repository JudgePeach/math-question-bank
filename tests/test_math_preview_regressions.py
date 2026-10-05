"""Execute the complete preview preprocessing path and the bundled KaTeX parser."""

import json
from pathlib import Path
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_preview_script(assertions):
    source = (PROJECT_ROOT / "static/js/editor.js").read_text(encoding="utf-8")
    start = source.index("function cleanChoiceStemParentheses(text)")
    marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    end = source.index(marker, start) + len(marker)
    katex_path = str(PROJECT_ROOT / "static/lib/katex/katex.min.js")
    script = (
        "process.on('uncaughtException', error => { console.error(error.stack); process.exit(1); });\n"
        + "global.window = {MathBankSafe: {safeImageUrl(v) { return v; }, escapeAttribute(v) { return v; }}};\n"
        + "const katex = require(" + json.dumps(katex_path) + ");\n"
        + source[start:end]
        + r'''
const assert = require('node:assert/strict');
function parsedFormulas(html) {
    const formulas = [];
    assert(!html.includes('MATH_PLACEHOLDER'), `placeholder leaked: ${html}`);
    replaceDelimitedMathForPreview(html, (whole, inner, opening) => {
        const formula = inner.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        const rendered = katex.renderToString(formula, {
            throwOnError: true, displayMode: opening === '$$' || opening === '\[',
        });
        assert(rendered.includes('katex'), `KaTeX did not render ${formula}`);
        formulas.push(formula);
        return whole;
    });
    return formulas;
}
'''
        + assertions
    )
    node = shutil.which("node")
    assert node, "Node.js is required for executable preview regressions"
    result = subprocess.run(
        [node, "-"], input=script, cwd=PROJECT_ROOT, text=True, encoding="utf-8",
        capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_currency_dollars_do_not_capture_prose_or_choices():
    run_preview_script(r'''
const stem = "The price is $90.00. Jack rings up $90.00 and adds 6% tax. Jill rings up $90.00, subtracts 20% of the price. What is Jack's total minus Jill's total?\n$\n\n";
const choices = String.raw`\begin{choices}
\item \ -\textdollar 1.06
\item \ -\textdollar 0.53
\item \ \textdollar 0
\item \ \textdollar 0.53
\item \ \textdollar 1.06
\end{choices}`;
const html = preprocessFormulaForKaTeX(stem + choices);
assert(html.includes("and adds 6% tax. Jill rings up"));
assert(!html.includes(String.raw`\ -`));
assert(!html.includes(String.raw`\ \(`));
assert.equal((html.match(/class="choices-label/g) || []).length, 5);
assert.equal(parsedFormulas(html).length, 9);
assert(parsedFormulas(html).every(formula => formula === String.raw`\text{\$}`));
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('Price $5 and $10; compute $2+3$ or $5$.')), [String.raw`\text{\$}`, String.raw`\text{\$}`, '2+3', '5']);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('The price is $5')), [String.raw`\text{\$}`]);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('Costs $5, $10, and $15.')), Array(3).fill(String.raw`\text{\$}`));
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('Price $5 total\n$\n\n' + String.raw`\begin{choices}\item $x+1$\end{choices}`)), [String.raw`\text{\$}`, String.raw`\text{\$}`, 'x+1']);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(String.raw`$-\textdollar 1.06$`)), [String.raw`-\textdollar 1.06`]);
const protectedText = String.raw`\begin{tikzpicture}\node {$5};\end{tikzpicture} ![](/static/uploads/$5.png) [[MBM_1]]`;
assert.equal(normalizePreviewDollarSigns(protectedText), protectedText);
''')


def test_currency_at_line_end_preserves_math_in_the_next_sentence():
    run_preview_script(r'''
for (const newline of ['\n', '\r\n', '\n\n']) {
    for (const amount of ['$5.', '$5', '$90.00.']) {
        const source = 'Assume each cost ' + amount + newline + 'Then $x^2=y$. Now...';
        const html = preprocessFormulaForKaTeX(source);
        assert.deepEqual(parsedFormulas(html), [String.raw`\text{\$}`, 'x^2=y']);
        assert(html.includes('Then $x^2=y$. Now...'), html);
    }
    const adjacentMath = preprocessFormulaForKaTeX('Cost $5.' + newline + '$x^2=y$.');
    assert.deepEqual(parsedFormulas(adjacentMath), [String.raw`\text{\$}`, 'x^2=y']);
    for (const formula of ['$5+' + newline + '6=11$', '$5' + newline + '+6=11$']) {
        assert.equal(preprocessFormulaForKaTeX(formula), formula);
        assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(formula)), [formula.slice(1, -1)]);
    }
}
''')

def test_prose_fillin_keeps_adjacent_math_comparisons_and_later_table_independent():
    run_preview_script(r'''
const source = '则$\\bar{x}$\\fillin 91（填“$>$”“$=$”或“$<$”）\n\n'
    + '随后评分表：\\begin{tabular}{cc}甲 & 93 \\\\ 丙 & $k$\\end{tabular}。';
const html = preprocessFormulaForKaTeX(source);
assert.deepEqual(parsedFormulas(html),
    ['\\bar{x}', '\\underline{\\hspace{1.5cm}}', '\\gt ', '=', '\\lt ', 'k']);
assert.equal((html.match(/<table\b/g) || []).length, 1);
assert(html.includes('随后评分表') && html.includes('甲') && html.includes('丙'));
assert(!html.includes('\\begin{tabular}'));
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('$x$\\fillin$y$')),
    ['x', '\\underline{\\hspace{1.5cm}}', 'y']);
''')


def test_fillin_preserves_all_complete_math_shells_and_macro_options():
    run_preview_script(r'''
for (const [opening, closing] of [['$', '$'], ['$$', '$$'], ['\\(', '\\)'], ['\\[', '\\]']]) {
    const source = opening + 'x+\\fillin[2cm][y]' + closing;
    const formatted = transformFillinMacro(source);
    assert.equal(formatted, opening + 'x+\\underline{\\hspace{2cm}y\\hspace{2cm}}' + closing);
    assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(source)),
        ['x+\\underline{\\hspace{2cm}y\\hspace{2cm}}']);
}
const blanks = '\\fillin[3cm]，\\fillin[a]；$x$\\fillin。$y$';
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(blanks)), [
    '\\underline{\\hspace{3cm}}', '\\underline{\\quad a \\quad}', 'x',
    '\\underline{\\hspace{1.5cm}}', 'y',
]);
const tick = String.fromCharCode(96);
const protectedExamples = [
    tick + '\\fillin' + tick, tick.repeat(3) + 'tex\n\\fillin\n' + tick.repeat(3),
    '\\verb|\\fillin|', '\\detokenize{\\fillin}',
    '\\begin{tikzpicture}\\node{\\fillin};\\end{tikzpicture}',
    '![](/static/uploads/fillin.png)', '[[MBM_scope_0001]]',
    '<mathbank-math id="MBM_scope_0001">\\fillin</mathbank-math>',
    '\\\\fillin',
];
for (const literal of protectedExamples) assert.equal(transformFillinMacro(literal), literal);
for (let count=1; count<=6; count++) {
    assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('11'+'\\fillin'.repeat(count))),
        Array(count).fill('\\underline{\\hspace{1.5cm}}'));
}
''')

def test_fillin_only_unwraps_a_complete_math_hint_within_its_own_option():
    run_preview_script(r'''
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('\\fillin[$2$]')),
    ['\\underline{\\quad 2 \\quad}']);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('\\fillin[2cm][$\\frac{1}{2}$]')),
    ['\\underline{\\hspace{2cm}\\frac{1}{2}\\hspace{2cm}}']);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('\\(x+\\fillin[$2$]\\)')),
    ['x+\\underline{\\quad 2 \\quad}']);
const compound='\\fillin[$x$+$y$]';
assert.equal(transformFillinMacro(compound), compound);
const adjacent='\\fillin[$2$]$z$';
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(adjacent)),
    ['\\underline{\\quad 2 \\quad}', 'z']);
''')

def test_fillin_in_table_cells_and_text_math_keeps_their_boundaries():
    run_preview_script(r'''
const table = '\\begin{tabular}{cc}$x$\\fillin & $y+\\fillin$\\end{tabular}';
const html = preprocessFormulaForKaTeX(table);
assert.equal((html.match(/<td\b/g) || []).length, 2);
assert.deepEqual(parsedFormulas(html),
    ['x', '\\underline{\\hspace{1.5cm}}', 'y+\\underline{\\hspace{1.5cm}}']);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('$\\text{已知}\\fillin$')),
    ['\\text{已知}\\underline{\\hspace{1.5cm}}']);
for (const [opening, closing] of [['$', '$'], ['$$', '$$'], ['\\(', '\\)'], ['\\[', '\\]']]) {
    const punctuation = preprocessFormulaForKaTeX(opening + '\\fillin，' + closing);
    assert.deepEqual(parsedFormulas(punctuation), ['\\underline{\\hspace{1.5cm}}']);
    assert(punctuation.endsWith('，'));
}
// The established path renders an unmatched dollar literally; this fix must
// not guess a formula or remove that visible dollar in old incomplete input.
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('$x+\\fillin')),
    ['\\text{\\$}', '\\underline{\\hspace{1.5cm}}']);
''')


def test_currency_recognition_preserves_closed_numerical_products():
    run_preview_script(r'''
for (const formula of [
    '$2 xy+3$', '$2 xy + 3$', '$2 xy$', '$2 AB$', '$2 xyz$', '$2 xy z+3$',
    '$2 xy_{1}+3$', String.raw`$2 xy\cdot 3$`, '$2 tax+3$', '$2 xy +\n3$',
    '$2 xy+3.5$', '$2 xy+3.$', '$2 xy+3; z$',
]) {
    const html = preprocessFormulaForKaTeX(formula);
    assert.equal(html, formula, html);
    assert.deepEqual(parsedFormulas(html), [formula.slice(1, -1)]);
}
const mixture = 'Price $5 and $10; compute $2 xy+3$ or $5$.';
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(mixture)),
    [String.raw`\text{\$}`, String.raw`\text{\$}`, '2 xy+3', '5']);
const taxSentence = 'Cost $5 total + tax. Compute $x+1$.';
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(taxSentence)), [String.raw`\text{\$}`, 'x+1']);
''')


def test_currency_normalization_keeps_literal_paths_code_and_locks():
    run_preview_script(r'''
for (const source of [
    '`$2 xy+3$ and $5`', '```text\n$5 and $10\n```',
    String.raw`\begin{tikzpicture}\node {$5 and $10};\end{tikzpicture}`,
    '![](/static/uploads/$5.png)', '[price](https://example.test/$5)',
    'https://example.test/$5?total=10', '[[MBM_scope-1_0001]]',
    '<mathbank-math id="MBM_scope_0001">$5 and $10</mathbank-math>',
    String.raw`\($2 xy+3$\)`, String.raw`\[$5 and $10\]`, '$$2 xy+3$$',
]) assert.equal(normalizePreviewDollarSigns(source), source, source);
for (const literal of [
    '`$5 and $10`', '```text\n$5 and $10\n```',
    '![](/static/uploads/$5.png)', '[price](/help/$5)',
    'https://example.test/$5?total=10', 'HTTPS://example.test/$5?total=10', '[[MBM_scope-1_0001]]',
]) {
    const source = literal + ' 计算 $2 xy+3$。';
    const html = preprocessFormulaForKaTeX(source);
    assert.deepEqual(parsedFormulas(html), ['2 xy+3']);
    if (literal.startsWith('!')) assert(html.includes('/static/uploads/$5.png'), html);
    if (literal.startsWith('http')) assert(html.includes(literal), html);
    assert.equal(cleanChoiceStemParentheses(source), source, source);
}
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('[$2 xy+3$](/help/$5)')),
    ['2 xy+3']);
const mathWithUrl = String.raw`$\text{https://example.test/x}$`;
assert.equal(normalizeNakedMathForPreview(mathWithUrl), mathWithUrl);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(mathWithUrl)), [mathWithUrl.slice(1, -1)]);
const environmentWithUrl = String.raw`\begin{align}x&=\text{https://example.test/x}\end{align}`;
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(environmentWithUrl)),
    [String.raw`\begin{aligned}x&=\text{https://example.test/x}\end{aligned}`]);
''')


def test_preview_literals_are_safe_inert_nodes_with_exact_visible_content():
    run_preview_script(r'''
const literals = [
    '`$5 and $10`', '``literal ` $5 and $10``', '~~~tex\n$5 and $10\n~~~',
    String.raw`\verb|$5 and $10|`, String.raw`\lstinline[language=TeX]|$5 and $10|`,
    String.raw`\mintinline{tex}|$5 and $10|`, String.raw`\mintinline{tex}{$5 and ${10}}`,
    String.raw`\detokenize{$5 and ${10}}`, String.raw`\path{dir/$5}`,
    String.raw`\begin{verbatim*}$5 and $10\end{verbatim*}`,
    String.raw`\begin{Verbatim*}$5 and $10\end{Verbatim*}`,
    String.raw`\begin{tikzpicture}\node {$5};\draw (0,0)--(1,1);\end{tikzpicture}`,
    'HTTPS://example.test/$5?total=10',
    '`<img src=x onerror=alert(1)>$5</code>`',
];
for (const literal of literals) {
    const source = literal + ' 然后 $x^2$。';
    const html = preprocessFormulaForKaTeX(source);
    assert(html.includes('<code class="mb-preview-literal">'), html);
    assert.deepEqual(parsedFormulas(html), ['x^2']);
    const visible = html.match(/<code class="mb-preview-literal">([\s\S]*?)<\/code>/)[1]
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    assert.equal(visible, literal);
    assert(!html.includes('<img src=x'), html);
}
for (const [literal, visible] of [
    ['<CODE class="x">$5 and $10</code>', '$5 and $10'],
    ['<pre>$5 and $10</PRE>', '$5 and $10'],
]) {
    const html = preprocessFormulaForKaTeX(literal + ' 然后 $x^2$。');
    assert(html.includes('<code class="mb-preview-literal">' + visible + '</code>'), html);
    assert.deepEqual(parsedFormulas(html), ['x^2']);
}
const collision = '\uE002L0\uE003 $2 xy+3$ ' + '`$5`';
const collisionHtml = preprocessFormulaForKaTeX(collision);
assert(collisionHtml.includes('\uE002L0\uE003'), collisionHtml);
assert.deepEqual(parsedFormulas(collisionHtml), ['2 xy+3']);
''')


def test_tex_spaces_do_not_consume_paired_hard_line_breaks():
    run_preview_script(r'''
for (const spaces of [' ', '  ', ' \t']) {
    const source = String.raw`甲\\` + spaces + '乙 $2 xy+3$';
    const html = preprocessFormulaForKaTeX(source);
    assert.equal((html.match(/<br>/g) || []).length, 1, html);
    assert(html.includes('<br>' + spaces + '乙'), html);
    assert.deepEqual(parsedFormulas(html), ['2 xy+3']);
}
assert.equal(preprocessFormulaForKaTeX(String.raw`甲\\\\ 乙`), '甲<br><br> 乙');
assert.equal(preprocessFormulaForKaTeX(String.raw`甲\ 乙`), '甲&nbsp;乙');
assert.equal(preprocessFormulaForKaTeX(String.raw`甲\\\ 乙`), '甲<br>&nbsp;乙');
const formula = String.raw`$\begin{aligned}x&=1\\ y&=2\end{aligned}$`;
assert.equal(normalizePreviewDollarSigns(formula), formula);
assert.equal((preprocessFormulaForKaTeX(formula).match(/<br>/g) || []).length, 0);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(formula)), [formula.slice(1, -1)]);
''')


def test_tables_keep_existing_formulas_and_repair_only_naked_cell_math():
    run_preview_script(r'''
const proper = String.raw`\begin{tabular}{cc} $x_1$ & $y_1$ \\ $x_2$ & $y_2$ \end{tabular}`;
const html = preprocessFormulaForKaTeX(proper);
assert.equal((html.match(/<td\b/g) || []).length, 4);
assert.deepEqual(parsedFormulas(html), ['x_1', 'y_1', 'x_2', 'y_2']);

const mixed = String.raw`\begin{tabular}{cc} $\frac{1}{2}$ & y_1 \\ \multicolumn{2}{c}{$x<1$} \\ \multirow{2}{*}{$z^2$} & $a$ \\ & b_1 \end{tabular}`;
const mixedHtml = preprocessFormulaForKaTeX(mixed);
assert(mixedHtml.includes('colspan="2"'));
assert(mixedHtml.includes('rowspan="2"'));
assert.deepEqual(parsedFormulas(mixedHtml), [String.raw`\frac{1}{2}`, 'y_1', String.raw`x\lt 1`, 'z^2', 'a', 'b_1']);
''')


def test_standalone_math_uses_katex_compatible_display_environments():
    run_preview_script(r'''
const environments = [
    ['equation', 'x=1'], ['equation*', 'x=1'],
    ['align', String.raw`x&=1\\y&=2`], ['align*', String.raw`x&=1\\y&=2`],
    ['alignat', String.raw`{1}x&=1\\y&=2`], ['alignat*', String.raw`{1}x&=1\\y&=2`],
    ['gather', String.raw`x=1\\y=2`], ['gather*', String.raw`x=1\\y=2`],
    ['multline', String.raw`x+y+z\\=1`], ['multline*', String.raw`x+y+z\\=1`],
];
for (const [environment, body] of environments) {
    const source = '\\begin{' + environment + '}' + body + '\\end{' + environment + '}';
    const html = preprocessFormulaForKaTeX(source);
    assert(html.startsWith('$$') && html.endsWith('$$'), `not display math: ${html}`);
    assert.equal(parsedFormulas(html).length, 1);
    const normalized = normalizeNakedMathForPreview(source);
    assert.equal(normalizeNakedMathForPreview(normalized), normalized, `not idempotent: ${environment}`);
}
''')


def test_nested_math_existing_delimiters_typography_and_image_layouts_remain_intact():
    run_preview_script(r'''
const nested = String.raw`\begin{cases} x & x>0 \\ \begin{cases} y & y>0 \\ 0 & y=0 \end{cases} & x<0 \end{cases}`;
for (const source of [nested, '\\begin{equation}f(x)=' + nested + '\\end{equation}']) {
    assert.equal(parsedFormulas(preprocessFormulaForKaTeX(source)).length, 1);
}
const multiline = '$x^2+\ny^2=1$';
assert.equal(preprocessFormulaForKaTeX(multiline), multiline);
assert.equal(parsedFormulas(preprocessFormulaForKaTeX(multiline)).length, 1);
const upright = String.raw`已知向量 $\mathbf{a}$。`;
assert.equal(preprocessFormulaForKaTeX(upright), upright);
assert.equal(parsedFormulas(preprocessFormulaForKaTeX(upright)).length, 1);
const escaped = String.raw`价格 \$5，公式 $x+\text{\$5}$，以及 $y_1$。`;
assert.equal(preprocessFormulaForKaTeX(escaped), escaped);
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(escaped)), [String.raw`x+\text{\$5}`, 'y_1']);
assert.equal(parsedFormulas(preprocessFormulaForKaTeX('$a$$b$$c$')).length, 3);
assert(preprocessFormulaForKaTeX(String.raw`题干 \paren`).includes('exam-zh-paren-preview'));
const image = preprocessFormulaForKaTeX('图 ![](/static/uploads/figure.png)', {
    '/static/uploads/figure.png': {align: 'right', size: 'large'},
});
assert(image.includes('mb-inline-image-align-right'));
assert(image.includes('mb-inline-image-size-large'));
''')


def test_parallelogram_uses_local_vector_and_semantic_mathml_in_every_math_style():
    run_preview_script(r'''
const samples = [
    String.raw`\parallelogram ABCD`, '▱ABCD', String.raw`\text{▱}ABCD`,
    String.raw`\text{A▱B}`, String.raw`\text{\parallelogram}`, String.raw`x^\parallelogram`,
    String.raw`S_{▱ABCD}`, String.raw`\dfrac{\parallelogram ABCD}{▱EFGH}`,
    String.raw`\sqrt{▱}`, String.raw`\overline{▱ABCD}`, String.raw`\color{red}{▱}`,
    String.raw`a_{▱}^{▱^{▱}}`,
];
for (const formula of samples) {
    const rendered = katex.renderToString(formula, {throwOnError: true, strict: 'error', trust: false});
    assert(rendered.includes('mb-parallelogram'), `no local vector: ${formula}`);
    assert(rendered.includes('<svg') && rendered.includes('<path'), `missing vector geometry: ${formula}`);
    assert(rendered.includes('>▱</mo>'), `missing semantic symbol: ${formula}`);
    assert(!rendered.includes('_fallback'), `system font fallback: ${formula}`);
}
const raw = String.raw`在 ▱ABCD 中；在 \parallelogram EFGH 中；▱$IJKL$。`;
const preview = preprocessFormulaForKaTeX(raw);
assert.deepEqual(parsedFormulas(preview), ['▱', String.raw`\parallelogram EFGH`, '▱', 'IJKL']);
assert.equal(normalizeNakedMathForPreview(normalizeNakedMathForPreview(raw)), normalizeNakedMathForPreview(raw));
const protectedSource = '代码 `▱`，图片 ![▱](/static/uploads/▱.png)，锁 [[MBM_math_1]]。';
assert.equal(normalizeNakedMathForPreview(protectedSource), protectedSource);
assert.equal(normalizeNakedMathForPreview('Let ▱ABCD be a parallelogram.'), String.raw`Let \(▱\)ABCD be a parallelogram.`);
for (const bold of [String.raw`\textbf{▱ABCD}`, String.raw`\textbf{\parallelogram ABCD}`]) {
    const rendered = preprocessFormulaForKaTeX(bold);
    assert(rendered.includes('<strong>'));
    assert.equal(parsedFormulas(rendered).length, 1);
}
const tikz = String.raw`\begin{tikzpicture}\node {▱};\end{tikzpicture}`;
assert.equal(normalizeNakedMathForPreview(tikz), tikz);
const table = String.raw`\begin{tabular}{cc} ▱ABCD & $\parallelogram EFGH$ \\ $\text{▱}$ & $S_{▱}$ \end{tabular}`;
assert.equal(parsedFormulas(preprocessFormulaForKaTeX(table)).length, 4);
const untrusted = katex.renderToString(String.raw`\href{javascript:alert(1)}{\parallelogram}`, {throwOnError: false, trust: false});
assert(!untrusted.includes('<a '), 'symbol support enabled untrusted HTML commands');
''')
