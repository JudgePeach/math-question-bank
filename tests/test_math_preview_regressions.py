"""Execute the complete preview preprocessing path and the bundled KaTeX parser."""

import json
from pathlib import Path
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_preview_script(assertions):
    source = (PROJECT_ROOT / "static/js/editor.js").read_text(encoding="utf-8")
    start = source.index("function transformFillinMacro(clean)")
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
        [node, "-"], input=script, cwd=PROJECT_ROOT, text=True,
        capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
