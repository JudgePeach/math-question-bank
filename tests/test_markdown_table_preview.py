"""Strict Markdown tables share the offline formula and literal renderer."""

from test_math_preview_regressions import run_preview_script


def test_distribution_table_keeps_rows_math_pipes_and_source_text():
    run_preview_script(r'''
const source = String.raw`因此 $X$ 的分布列为

| $X$ | $-1$ | $0$ | $1$ |
| --- | :--- | :---: | ---: |
| $P$ | $(1-\alpha)\beta$ | $P(A|B)$ | $\{x\mid x>0\}$ |

最后由 $P(A)=\dfrac{1}{2}$ 得结论。`;
const before = source;
const html = preprocessFormulaForKaTeX(source);
assert.equal(source, before);
assert.equal((html.match(/<table\b/g)||[]).length, 1);
assert.equal((html.match(/<tr>/g)||[]).length, 2);
assert.equal((html.match(/<th\b/g)||[]).length, 4);
assert.equal((html.match(/<td\b/g)||[]).length, 4);
assert(html.includes('text-left') && html.includes('text-right'));
assert.equal((html.match(/mb-preview-paragraph-break/g)||[]).length, 0);
assert(html.indexOf('分布列为') < html.indexOf('<table'));
assert(html.indexOf('</table>') < html.indexOf('最后由'));
assert(!html.includes('| ---'));
assert(!/[\uE002\uE003]/.test(html), html);
assert.deepEqual(parsedFormulas(html), [
    'X', 'X', '-1', '0', '1', 'P', '(1-\\alpha)\\beta',
    'P(A|B)', '\\{x\\mid x\\gt 0\\}', 'P(A)=\\dfrac{1}{2}'
]);
''')


def test_table_cells_preserve_literals_images_escaped_pipes_and_naked_math():
    run_preview_script(r'''
const source = String.raw`| 项目 | 值 | 图片 |
| --- | --- | --- |
| 左\|右 | $|a|+\lvert b\rvert$ | ![原|图](/static/uploads/table.png) |
| 代码 | \verb|x| 和 ` + '`$literal|value$`' + String.raw` | x_1 |`;
const layouts = {'/static/uploads/table.png':{align:'right',size:'large'}};
const html = preprocessFormulaForKaTeX(source, layouts);
assert.equal((html.match(/<th\b/g)||[]).length, 3);
assert.equal((html.match(/<td\b/g)||[]).length, 6);
assert(html.includes('左|右'));
assert(html.includes('alt="原|图"'));
assert(html.includes('mb-inline-image-align-right'));
assert(html.includes('mb-inline-image-size-large'));
assert.equal((html.match(/<img\b/g)||[]).length, 1);
assert.equal((html.match(/mb-preview-literal/g)||[]).length, 2);
assert.deepEqual(parsedFormulas(html), ['|a|+\\lvert b\\rvert', 'x_1']);
assert(!/[\uE002\uE003]/.test(html), html);
''')


def test_wide_table_retains_every_column_empty_cell_and_scroll_container():
    run_preview_script(r'''
const row = cells => '| ' + cells.join(' | ') + ' |';
const header = Array.from({length:12},(_,i)=>'第'+(i+1)+'列');
const first = Array.from({length:12},(_,i)=>String(i+1));first[5]='';
const second = Array.from({length:12},(_,i)=>String(i+13));
const source = [row(header),row(header.map(()=>'---')),row(first),row(second)].join('\r\n');
const html = preprocessFormulaForKaTeX(source);
assert.equal((html.match(/<th\b/g)||[]).length, 12);
assert.equal((html.match(/<td\b/g)||[]).length, 24);
assert(html.includes('overflow-x-auto') && html.includes('max-w-full'));
assert(html.includes('whitespace-nowrap'));
assert(!html.includes('text-xs'));
assert(html.includes('>第12列</th>') && html.includes('>24</td>'));
assert(/<td\b[^>]*><\/td>/.test(html));
assert(!/[\uE002\uE003]/.test(html), html);
''')


def test_only_complete_rectangular_outer_pipe_tables_are_converted():
    run_preview_script(r'''
for (const source of [
    '| X | P |\n| 0 | 1 |',
    '| X | P |\n| --- | --- |',
    '| X | P |\n| --- | --- |\n| 0 | 1 |\n| 1 | 2 | 3 |',
    '| X | P |\n| --- | --- |\n| 0 | 1',
    '| X |\n| --- |\n| 1 |',
    'X | P\n--- | ---\n0 | 1',
    '    | X | P |\n    | --- | --- |\n    | 0 | 1 |',
    '| X | P |\n| --- | --- |\n| 0 | 1\\|',
    '| X | P |\n| --- | --- |\n| 0 | 1 |\n| --- | --- |',
    '| X | P |\n| --- | --- |\n| $x | y |',
    '| X | P |\n| --- | --- |\n| `x | y |',
    '| X | P |\n| --- | --- |\n| [x | y |',
]) {
    const html = preprocessFormulaForKaTeX(source);
    assert.equal((html.match(/<table\b/g)||[]).length, 0, html);
    assert(!/[\uE002\uE003]/.test(html), html);
}
''')


def test_existing_math_code_tikz_and_tex_environments_do_not_supply_tables():
    run_preview_script(r'''
const table = '| X | P |\n| --- | --- |\n| 0 | $1$ |';
for(const source of [
    '```markdown\n'+table+'\n```',
    '~~~~\n'+table+'\n~~~~',
    '`\n'+table+'\n`',
    '``\n'+table+'\n``',
    '<pre>\n'+table+'\n</pre>',
    '<code>\n'+table+'\n</code>',
    String.raw`\begin{tikzpicture}`+'\n'+table+'\n'+String.raw`\end{tikzpicture}`,
    String.raw`\begin{verbatim}`+'\n'+table+'\n'+String.raw`\end{verbatim}`,
    String.raw`\[`+'\n'+table+'\n'+String.raw`\]`,
    String.raw`\begin{choices}\item `+'\n'+table+'\n'+String.raw`\end{choices}`,
    String.raw`\begin{custom}`+'\n'+table+'\n'+String.raw`\end{custom}`,
    String.raw`\begin{custom}`+'\n'+table,
    '<table>\n'+table+'\n</table>',
]) {
    const html = preprocessFormulaForKaTeX(source);
    assert.equal((html.match(/<table\b/g)||[]).length, 0, html);
    assert(!/[\uE002\uE003]/.test(html), html);
}
const multilineCode=preprocessFormulaForKaTeX('`\n'+table+'\n`');
assert.equal((multilineCode.match(/mb-preview-literal/g)||[]).length, 1);
assert.deepEqual(parsedFormulas(multilineCode), []);
const existing = String.raw`\begin{tabular}{cc}$x$ & $y$\\甲 & 乙\end{tabular}`;
const mixed = preprocessFormulaForKaTeX(existing+'\n\n'+table);
assert.equal((mixed.match(/<table\b/g)||[]).length, 2);
assert.deepEqual(parsedFormulas(mixed), ['x','y','1']);
const adjacent = preprocessFormulaForKaTeX(table+'\n\n'+table);
assert.equal((adjacent.match(/<table\b/g)||[]).length, 2);
assert.deepEqual(parsedFormulas(adjacent), ['1','1']);
assert(!/[\uE002\uE003]/.test(adjacent), adjacent);
''')


def test_balanced_bare_math_groups_are_not_column_boundaries():
    run_preview_script(r'''
const source = String.raw`| 概率 | 文字 | 区间 |
| --- | --- | --- |
| P(A|B) | \text{a|b} | [10000,+∞) |`;
const html = preprocessFormulaForKaTeX(source);
assert.equal((html.match(/<td\b/g)||[]).length, 3);
assert.deepEqual(parsedFormulas(html), ['P(A|B)', '\\text{a|b}']);
assert(html.includes('[10000,+∞)'));
const row = cells => '| '+cells.join(' | ')+' |';
const header = Array.from({length:33},(_,i)=>'列'+i);
const overLimit = [row(header),row(header.map(()=>'---')),row(header)].join('\n');
assert.equal((preprocessFormulaForKaTeX(overLimit).match(/<table\b/g)||[]).length,0);
''')


def test_normal_markdown_links_keep_labels_and_literal_urls_in_their_cells():
    run_preview_script(r'''
for(const url of ['https://example.test', 'https://example.test/a|b', 'https://example.test/$a|b$']) {
    const source = '| [A]('+url+') | C |\n| --- | --- |\n| [x]('+url+') | z |';
    const html = preprocessFormulaForKaTeX(source);
    assert.equal((html.match(/<table\b/g)||[]).length,1,html);
    assert.equal((html.match(/<th\b/g)||[]).length,2,html);
    assert.equal((html.match(/<td\b/g)||[]).length,2,html);
    assert(html.includes('[A') && html.includes('[x') && html.includes(url),html);
    assert.equal((html.match(/mb-preview-literal/g)||[]).length,2,html);
    assert(!html.includes('<a') && !html.includes('href='),html);
    assert.deepEqual(parsedFormulas(html),[]);
    assert(!/[\uE002\uE003]/.test(html),html);
}
const mathLabel=preprocessFormulaForKaTeX(
    '| 标签 | 值 |\n| --- | --- |\n| [$x$](https://example.test/a|b) | $y$ |');
assert.equal((mathLabel.match(/<td\b/g)||[]).length,2);
assert.deepEqual(parsedFormulas(mathLabel),['x','y']);
''')


def test_multiline_math_inside_one_cell_and_marker_looking_text_survive():
    run_preview_script(r'''
const source = '| 值 | 公式 |\n| --- | --- |\n| '+ '\uE002T0\uE003'
    + ' | $\\begin{aligned}x&=1\\\\\ny&=2\\end{aligned}$ |';
const html = preprocessFormulaForKaTeX(source);
assert.equal((html.match(/<td\b/g)||[]).length, 2);
assert(html.includes('\uE002T0\uE003'));
assert.deepEqual(parsedFormulas(html), ['\\begin{aligned}x&=1\\\\\ny&=2\\end{aligned}']);
''')
