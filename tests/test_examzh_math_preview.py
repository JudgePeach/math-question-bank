"""Check exam-zh constants through the shared preview and bundled KaTeX."""

from test_math_preview_regressions import run_preview_script


def test_examzh_constants_keep_upright_semantics_in_nested_formulas():
    run_preview_script(r'''
const options = {throwOnError: true, strict: 'error', trust: false};
const source = String.raw`\eu^{\iu\pi}+\upe^{\upi\pi}=\dfrac{\iu^2}{\eu}+i+e`;
const html = katex.renderToString(source, options);
assert(!html.includes('_fallback') && !html.includes('katex-error'));
assert(html.includes('<mi mathvariant="normal">i</mi>'));
assert(html.includes('<mi mathvariant="normal">e</mi>'));
assert(html.includes('<mi>i</mi>') && html.includes('<mi>e</mi>'));
assert(html.includes('mathnormal'));
for (const alias of [String.raw`\iu`, String.raw`\upi`]) {
    assert.equal(katex.renderToString(alias, options).split('katex-html')[1],
        katex.renderToString(String.raw`\mathrm{i}`, options).split('katex-html')[1]);
}
for (const alias of [String.raw`\eu`, String.raw`\upe`]) {
    assert.equal(katex.renderToString(alias, options).split('katex-html')[1],
        katex.renderToString(String.raw`\mathrm{e}`, options).split('katex-html')[1]);
}
for (const display of [false, true]) {
    katex.renderToString(source, {...options, displayMode: display});
}
''')


def test_examzh_constants_render_in_prose_choices_tables_and_display_math():
    run_preview_script(r'''
const sources = [
    String.raw`复数 $z=2+3\iu$，求 $\eu^{\iu\pi}$。`,
    String.raw`复数 z=2+3\upi；自然常数 \upe。`,
    String.raw`\begin{choices}\item $\iu$\item $\upi^2$\item $\eu$\item $\upe^{-1}$\end{choices}`,
    String.raw`\begin{tabular}{cc}$\iu$ & $\eu$ \\ $\upi$ & $\upe$\end{tabular}`,
    String.raw`\[\dfrac{\iu}{\eu}+\upi+\upe\]`,
];
for (const source of sources) {
    const formulas = parsedFormulas(preprocessFormulaForKaTeX(source));
    assert(formulas.length > 0, source);
    assert(formulas.join(' ').includes(String.raw`\iu`) || formulas.join(' ').includes(String.raw`\upi`));
}
const plain = '变量 $i+e$ 保持原样。';
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(plain)), ['i+e']);
''')


def test_examzh_constants_respect_command_boundaries_and_protected_literals():
    run_preview_script(r'''
for (const unknown of [String.raw`\iux`, String.raw`\upix`, String.raw`\eux`, String.raw`\upex`, String.raw`\du`]) {
    assert.throws(() => katex.renderToString(unknown, {throwOnError: true}), /Undefined control sequence/);
}
const protectedSource = '`$\\iu$ \\eu`\n\n'
    + String.raw`\begin{tikzpicture}\node {$\upi+\upe$};\end{tikzpicture}`
    + String.raw` ![常数](/static/uploads/iu_eu.png) [[MBM_iu_eu]] https://example.test/iu_eu`;
assert.equal(normalizeNakedMathForPreview(protectedSource), protectedSource);
const source = protectedSource + '\n\n' + String.raw`真公式 $\eu^{\iu\pi}+1=0$。`;
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(source)), [String.raw`\eu^{\iu\pi}+1=0`]);
''')
