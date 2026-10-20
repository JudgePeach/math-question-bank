"""Spacing repairs must preserve TeX boundaries and mathematical styles."""

from test_math_preview_regressions import run_preview_script


def test_fraction_rows_keep_rules_labels_and_explicit_gaps_renderable():
    run_preview_script(r'''
const sources = [
    String.raw`$$\begin{array}{c|c}X&0\\[0.5em]\hline P&\dfrac{2}{9}\end{array}$$`,
    String.raw`$$\begin{aligned}x&=\frac{1}{3}\\[1em]y&=\frac{2}{3}\end{aligned}\tag{a}$$`,
    String.raw`$$\begin{align}x&=\dfrac{1}{3}\tag{1}\\y&=\dfrac{2}{3}\tag{2}\end{align}$$`,
    String.raw`$\begin{array}{cc}\dfrac12&\begin{array}{c}\dfrac23\\\dfrac45\end{array}\\1&\dfrac56\end{array}$`,
];
for (const source of sources) {
    const formulas = parsedFormulas(preprocessFormulaForKaTeX(source));
    assert.equal(formulas.length, 1);
    const html = katex.renderToString(formulas[0], {throwOnError:true, displayMode:true, trust:false});
    assert(html.includes('<mtable'));
    assert(html.includes('<mfrac>'));
    assert(html.includes('mathbackground="transparent"'));
}
const table = parsedFormulas(preprocessFormulaForKaTeX(sources[0]))[0];
assert(table.includes(String.raw`\\[0.5em]\hline`));
const aligned = parsedFormulas(preprocessFormulaForKaTeX(sources[1]))[0];
assert(aligned.includes(String.raw`\\[1em]`));
assert.equal((aligned.match(/\\tag\{/g)||[]).length, 1);
const tagged = parsedFormulas(preprocessFormulaForKaTeX(sources[2]))[0];
assert.equal((tagged.match(/\\tag\{/g)||[]).length, 2);
assert(tagged.includes(String.raw`\tag{1}\\`));
''')


def test_spacing_preserves_single_rows_scripts_and_literal_payloads():
    run_preview_script(r'''
for (const formula of [
    String.raw`\dfrac{x+1}{x-1}`,
    String.raw`\begin{pmatrix}\dfrac12&1\end{pmatrix}`,
    String.raw`\begin{array}{c}\dfrac12\\\end{array}`,
    String.raw`x^{\begin{smallmatrix}\dfrac12\\1\end{smallmatrix}}`,
    String.raw`\begin{array}{cc}1&2\\3&4\end{array}`,
]) {
    assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX('$'+formula+'$')), [formula]);
}
const example = String.raw`\begin{array}{c}\dfrac12\\\dfrac23\end{array}`;
const literals = '`$'+example+'$`\n\n'+String.raw`\begin{tikzpicture}\node {$\dfrac12$};\end{tikzpicture}`;
assert.equal(parsedFormulas(preprocessFormulaForKaTeX(literals)).length, 0);
assert(!preprocessFormulaForKaTeX(literals).includes('colorbox'));
const malformed = String.raw`\begin{array}{c}\dfrac12\\\dfrac23`;
assert.equal(padTallMathRowsForPreview('$'+malformed+'$'), '$'+malformed+'$');
''')


def test_commented_environment_tokens_and_math_struts_are_preserved():
    run_preview_script(r'''
const formula = String.raw`\begin{array}{l}\dfrac{1}{3}% hi\end{array}`
    + '\n' + String.raw`\\\dfrac{2}{3}\end{array}`;
const fixed = parsedFormulas(preprocessFormulaForKaTeX('$$'+formula+'$$'))[0];
assert(fixed.includes('% hi'+String.raw`\end{array}`+'\n'));
assert(fixed.includes('colorbox'));
const percentages = String.raw`\begin{array}{c}\dfrac12\% % \end{array}`
    + '\n' + String.raw`\\\dfrac23\end{array}`;
parsedFormulas(preprocessFormulaForKaTeX('$$'+percentages+'$$'));
const trailing = String.raw`\begin{array}{c}\dfrac12\\% no row` + '\n' + String.raw`\end{array}`;
assert.equal(padTallMathRowsForPreview('$$'+trailing+'$$'), '$$'+trailing+'$$');
const nestedComment = String.raw`\begin{array}{c}` + trailing + String.raw`\\\dfrac23\end{array}`;
assert(parsedFormulas(preprocessFormulaForKaTeX('$$'+nestedComment+'$$'))[0].includes(trailing));
const visiblePercent = String.raw`\begin{array}{c}50\%\dfrac13\\\dfrac23\end{array}`;
assert.equal((parsedFormulas(preprocessFormulaForKaTeX('$$'+visiblePercent+'$$'))[0].match(/colorbox/g)||[]).length, 2);
const struts = String.raw`\begin{array}{c}\strut\vphantom{\dfrac{a}{b}}\dfrac12\\\dfrac23\end{array}`;
const paddedStruts = parsedFormulas(preprocessFormulaForKaTeX('$$'+struts+'$$'))[0];
assert(paddedStruts.includes(String.raw`\strut\vphantom{\dfrac{a}{b}}`));
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(String.raw`$x+\strut\vphantom{y}$`)),
    [String.raw`x+\strut\vphantom{y}`]);
''')


def test_padded_cells_keep_text_and_display_fraction_and_operator_styles():
    run_preview_script(r'''
for (const [name, spec] of [['array','{c}'], ['pmatrix',''], ['aligned',''], ['gathered','']]) {
    const cell = String.raw`\frac{1}{2}+\sum_{n=1}^{5}n`;
    const source = '\\begin{'+name+'}'+spec+cell+String.raw`\\`+cell+'\\end{'+name+'}';
    const padded = parsedFormulas(preprocessFormulaForKaTeX('$$'+source+'$$'))[0];
    const options = {throwOnError:true,displayMode:true};
    const original = katex.renderToString(source, options);
    const repaired = katex.renderToString(padded, options);
    function fractionSizes(tree, result = []) {
        if (tree.classes && tree.classes.includes('mfrac')) {
            result.push([tree.height, tree.depth, tree.maxFontSize]);
        }
        (tree.children || []).forEach(child => fractionSizes(child, result));
        return result;
    }
    assert.deepEqual(fractionSizes(katex.__renderToDomTree(padded, options)),
        fractionSizes(katex.__renderToDomTree(source, options)), name);
    assert.equal((repaired.match(/<mfrac>/g)||[]).length, 2);
    assert.equal((repaired.match(/<munderover>/g)||[]).length,
        (original.match(/<munderover>/g)||[]).length, name);
}
''')
