"""Native guards inspect visible mathematical content, not literal payloads."""

from types import SimpleNamespace

import pytest

from mathbank import pdf_inspector_helper as helper


@pytest.mark.parametrize("source", [
    "1. 阅读代码 `设 ， 为`，再求 $x=3$ 的结果。",
    "1. 阅读下列代码。\n```text\nA. B. C. D.\n```\n2. 求 $x=3$ 的结果。",
    "1. 阅读下列代码。\n~~~text\n对边分别为 ， ，\n~~~\n2. 求 $x=3$ 的结果。",
    '1. <span title="A. B. C. D.">已知 $x=3$。</span>',
    '1. <code>设 ， 为</code> 是待讨论的字符串。',
    '1. <pre>\nA. B. C. D.\n</pre> 是示例内容。',
    '1. 已知 $x=3$，见 [资料](uploads/设 ， 为.txt)。',
    '1. 已知 $x=3$。<!-- A. B. C. D. -->',
])
def test_literal_payloads_are_not_missing_formula_evidence(source):
    assert not helper._has_math_formula_loss(source)


@pytest.mark.parametrize("content", [
    '`x`', '<code>x</code>', 'https://example.test/x',
    '<img src="formula.png" alt="公式" />', '![公式](formula.png)',
    '$x$', r'\(x\)', r'$\text{x}$', '<span>x</span>',
])
def test_diagnostic_masking_does_not_make_retained_content_slots_empty(content):
    assert not helper._has_math_formula_loss(f"1. 设 {content} ，为求函数值，先代入。")
    assert not helper._has_math_formula_loss(f"1. A. {content} B. {content} C. {content} D. {content}")


@pytest.mark.parametrize("source", [
    '1. 设 <span title="x"></span> ，为求函数值，先代入。',
    '1. 设 <!--x--> ，为求函数值，先代入。',
    '1. 设 <img> ，为求函数值，先代入。',
    '1. <span>A. B. C. D.</span>',
    '1. 设 ，为函数参数。另有示例 `x`。',
    '1. <code>完整的示例</code>\n2. 对边分别为 ， ，。',
    '1. [设 ，为函数参数](https://example.test/x)。',
])
def test_visible_loss_is_not_hidden_by_literals_elsewhere(source):
    assert helper._has_math_formula_loss(source)


@pytest.mark.parametrize("source", [
    '商品 A £20，商品 B £30，数量 x=3，求总价。',
    '预算范围如下：商品 A £20，商品 B £30。',
    '价目表：A £20；已知数量 x=3，求总价。',
    'Product A £20, Product B £30; x=3. Find the total cost.',
    'Price list: A £20; quantity x=3. Calculate the total.',
])
def test_labelled_currency_amounts_do_not_become_inequalities(source):
    assert helper.native_text_quality_reasons(source) == []


@pytest.mark.parametrize("source", [
    '预算 £100，变量满足 x £4。',
    '商品 A £20，数量 x 满足 x £4。',
    'Price list: A £20; x £4 and x=3.',
    '商品价格满足 2 £ x < 4。',
    '商品 A £x，变量 x=3。',
    '工件质量满足49.98££x50.02时，记为合格。',
    '函数 $f=ôxô$ 的定义域为实数集。',
    '1. 已知 $x=1$，另有关系 x \ufffd 1。',
])
def test_currency_label_cannot_excuse_separate_broken_math(source):
    assert helper.native_text_quality_reasons(source)


def test_literal_protection_keeps_valid_native_page_out_of_ocr(monkeypatch):
    source = '商品 A £20，数量 x=3。\n说明代码 `A. B. C. D.` 的字面内容。'
    monkeypatch.setattr(helper, '_PDF_INSPECTOR_AVAILABLE', True)
    monkeypatch.setattr(helper, 'pdf_inspector', SimpleNamespace(
        extract_pages_markdown=lambda *a, **k: SimpleNamespace(pages=[
            SimpleNamespace(page=0, markdown=source, needs_ocr=False),
        ]),
    ))
    result = helper.inspect_and_extract_pdf(b'synthetic')
    assert result['pages_needing_ocr'] == []
    assert result['pages'][0]['markdown'] == source
    assert result['pages'][0]['quality_reasons'] == []


def test_slot_geometry_receives_unmodified_source_even_when_probe_masks_literals(monkeypatch):
    source = '1. 已知函数 $x^2$，求值。另有代码 `A. B. C. D.`。'
    seen = []
    monkeypatch.setattr(helper, '_PDF_INSPECTOR_AVAILABLE', True)
    monkeypatch.setattr(helper, 'pdf_inspector', SimpleNamespace(extract_text_with_positions=lambda *a, **k: []))
    monkeypatch.setattr(helper, '_has_multiple_inline_formula_images', lambda items, markdown: seen.append(markdown) or False)
    assert not helper._has_math_formula_loss(source, pdf_path='synthetic.pdf')
    assert seen == [source]


@pytest.mark.parametrize('source', [
    '已知 $x+`\ue123`=2$，求值。',
    '$\\text{`\ue123`}=2$',
    '$x+\\text{https://example.test/\ue123}=2$',
    '$x+\\text{[示例](https://example.test/\ue123)}=2$',
    '$x+\\text{<span title="\ue123">}=2$',
    '$x+\\text{<code>\ue123</code>}=2$',
    '\\(x+`\ue123`=2\\)',
    '\\[x+`\ue123`=2\\]',
    '\\begin{aligned}x+`\ue123`&=2\\end{aligned}',
    'Price $5+`\ue123`=2$.',
    'Price $5`\ue123`=2$.',
    '价格 $5，`\ue123`=2$。',
    'Price $5 and $10; known $x+`\ue123`=2$.',
    '价格 $5，数量 $x+`\ue123`=2$。',
    '$5 and `\ue123`$10',
])
def test_math_content_is_not_masked_as_code_url_or_html_metadata(source):
    assert helper.native_text_quality_reasons(source)
    assert '\ue123' in helper._native_diagnostic_text(source)


@pytest.mark.parametrize('source', [
    '`$x+\ue123=2$` 是代码。',
    '``$x+`\ue123`=2$`` 是带反引号的代码。',
    '```text\n$x+`\ue123`=2$\n```\n已知 $x=3$。',
    '<code>$x+`\ue123`=2$</code> 是代码。',
    '<span title="$x+`\ue123`=2$">已知 $x=3$。</span>',
    'Price $5 and $10; code `$x+\ue123=2$`.',
    '价格 $5，代码 `$x+\ue123=2$`，另一个价格 $10。',
    r'Price \$5 and \$10; ' + '`$x+\ue123=2$` is code.',
    '已知 $5$ 和 $10$，代码 `$x+\ue123=2$` 保留原样。',
])
def test_code_math_strings_and_prices_do_not_create_math_spans(source):
    assert helper.native_text_quality_reasons(source) == []
    assert '\ue123' not in helper._native_diagnostic_text(source)


def test_math_and_literal_scope_is_decided_in_original_source_order():
    source = '`$x+\ue123=2$` 是代码，已知 $y+`\ue456`=3$，另有 `<span title="\ue789">`。'
    probe = helper._native_diagnostic_text(source)
    assert '\ue123' not in probe and '\ue789' not in probe
    assert '$y+`\ue456`=3$' in probe
    assert helper.native_text_quality_reasons(source)
