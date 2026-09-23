"""Execute the representative school-symbol catalogue against bundled KaTeX."""

import json
from pathlib import Path

from test_math_preview_regressions import run_preview_script


CATALOGUE = Path(__file__).parent / "fixtures" / "school_math_symbols.json"


def test_school_symbol_catalogue_and_explicit_font_or_custom_command_limits():
    samples = json.loads(CATALOGUE.read_text(encoding="utf-8"))["samples"]
    run_preview_script("const samples = " + json.dumps(samples, ensure_ascii=False) + r''';
const fontCommands = new Set(['blackboard_mathbbm', 'blackboard_mathds']);
let supported = 0;
for (const sample of samples) {
    const needsAuthoredDefinition = sample.scope === 'custom_shorthand' || fontCommands.has(sample.id);
    if (needsAuthoredDefinition) {
        assert.throws(() => katex.renderToString(sample.latex, {throwOnError: true}), /Undefined control sequence/);
        katex.renderToString(sample.alternative, {throwOnError: true, strict: 'error', trust: false});
        continue;
    }
    const rendered = katex.renderToString(sample.latex, {throwOnError: true, strict: 'error', trust: false});
    assert(!rendered.includes('_fallback'), `font fallback: ${sample.id}`);
    assert(!rendered.includes('katex-error'), `render error: ${sample.id}`);
    assert.equal(parsedFormulas(preprocessFormulaForKaTeX('$' + sample.latex + '$')).length, 1, sample.id);
    supported++;
}
assert.equal(supported, 114);
''')


def test_wideparen_preserves_base_macros_scripts_and_mathml_arc_semantics():
    run_preview_script(r'''
const inputs = [
    String.raw`\wideparen{AB}`, String.raw`\wideparen{ABCDEFGHIJ}`,
    String.raw`S_{\wideparen{AB}}`, String.raw`\frac{\wideparen{AB}}{\wideparen{CD}}`,
    String.raw`\wideparen{\wideparen{AB}+\wideparen{CD}}`,
    String.raw`\wideparen{\vec{AB}}`, String.raw`\wideparen{A_1B_2}`,
    String.raw`\def\myarc#1{\wideparen{#1}}\myarc{AB}`,
    String.raw`\overarc[1]{AB}`, String.raw`\color{red}{\wideparen{\mathbf{ABC}}}`,
];
for (const input of inputs) {
    const rendered = katex.renderToString(input, {throwOnError: true, strict: 'error', trust: false});
    assert(rendered.includes('mb-arc-accent'), input);
    assert(rendered.includes('<mover') && rendered.includes('>⏜</mo>'), input);
    assert(rendered.includes('<svg') && !rendered.includes('_fallback'), input);
}
assert.throws(() => katex.renderToString(String.raw`\overarc[2]{AB}`), /default width/);
assert.throws(() => katex.renderToString(String.raw`\overarc[1{AB}`), /default width/);
const bare = String.raw`圆弧 \wideparen{AB}；度分秒 \ang{30;15;20}。`;
assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(bare)), [String.raw`\wideparen{AB}`, String.raw`\ang{30;15;20}`]);
for (const gap of [' ', '\t', '\n']) {
    const spaced = '\\ang' + gap + '{30;15;20}';
    assert.deepEqual(parsedFormulas(preprocessFormulaForKaTeX(spaced)), [spaced]);
}
''')


def test_school_units_keep_numeric_arguments_text_and_protected_source():
    run_preview_script(r'''
for (const input of [String.raw`\ang{30}`, String.raw`\ang{-30.5}`, String.raw`\ang{30;15;20}`,
    String.raw`\ang{;15;20.5}`, String.raw`\ang{0;0;20}`, String.raw`20\celsius`, '20℃',
    String.raw`\text{20℃}`, '5‰', String.raw`\text{5\textperthousand}`, String.raw`5\permil`,
    String.raw`\sfrac{a+b}{c+d}`, String.raw`\frac{\sfrac{1}{2}}{3}`]) {
    const result = katex.renderToString(input, {throwOnError: true, strict: 'error', trust: false});
    assert(!result.includes('_fallback'), input);
}
for (const input of [String.raw`\ang{}`, String.raw`\ang{a}`, String.raw`\ang{30;15;20;1}`,
    String.raw`\ang[angle-symbol-over-decimal]{30}`]) {
    assert.throws(() => katex.renderToString(input), /ang/);
}
assert.equal(normalizeNakedMathForPreview('Rate 5‰ and temperature 20℃.'), String.raw`Rate 5\(‰\) and temperature 20\(℃\).`);
const locked = String.raw`![‰℃](/static/uploads/‰℃.png) [[MBM_1]]` + ' `\\wideparen{AB} \\ang{30;15;20} ‰ ℃`';
assert.equal(normalizeNakedMathForPreview(locked), locked);
''')
