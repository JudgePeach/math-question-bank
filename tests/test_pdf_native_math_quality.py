"""Reject proved native 2-D math loss without repairing digits or variables."""
from types import SimpleNamespace

import pytest

from pdf_feature_contract import experimental_pdf_only
from mathbank import pdf_inspector_helper as helper
from mathbank.pdf_native_math_quality import (
    native_math_layout_reasons, native_script_geometry_reasons, script_markup_probe,
)


@pytest.mark.parametrize('source', [
    '已知数列满足 b<sub>n+1</sub> = b<sub>n</sub> + ，则求通项。',
    '设变量满足 t = 3 × ，求值。',
    '已知 g(x)=ln(x+1)− ，求定义域。',
    '命中概率为 ，其余条件保持原样。',
    '椭圆 D: + = 1，求其焦点。',
    '由定义知 f(x) = = 2。',
    '由定义知 t=+=3。',
    '<sup>x</sup> 2 <sup>y</sup> 2 23．已知双曲线 D: 2 − 2 = 1，求离心率。',
    '<u>3q</u> 31．过焦点的弦长为 = 6q，求参数。',
    '每次命中的概率为 <sup>3</sup>， 5 另一人独立投篮。',
    '函数 g(x)=x−<sup>x</sup>，并令 t=<sup>y</sup>，求值。',
])
def test_retained_quality_component_detects_missing_slots_without_rewriting(source):
    before = str(source)
    # The component remains testable even though the restored upstream
    # inspector deliberately does not install this experimental quality gate.
    visible = helper._native_diagnostic_text(script_markup_probe(source))
    assert native_math_layout_reasons(visible)
    assert source == before


@pytest.mark.parametrize('source', [
    '函数 y = x² + 2x，且 x ≠ 1。',
    '设 f(x):=x+2，求 f(3)。',
    '程序表达式 y==x 的含义。',
    r'已知 $a_{n+1}=a_n+\frac{1}{a_n}$，求数列性质。',
    '<p>已知 x<sup>2</sup> + y<sup>2</sup> = 1，求值。</p>',
    '<span>x</span><sup>2</sup> + <span>y</span><sup>2</sup> = 4',
    '说明标记，<sup>a</sup>，以及另一标记，<sup>b</sup>。',
    'Question 23．已知 x=1，求 y。',
    'Exam2027 23．已知 x=1，求 y。',
    'The rating = A+，and the grade = B+，read the text.',
    'The sign: + = addition; the symbol =，read it literally.',
    '评分规则：+ = 1，- = 0。',
    '裸数学 N ≤ L 2，乘数写在字母之后也可以合法。',
    '读取 `x = 3 + ，` 与 `$C: + = 1$` 的字面内容。',
    '```text\n<sup>x</sup> 2 <sup>y</sup> 2 23．已知 D: + =1\n```\n正文。',
    '<code>概率为 <sup>3</sup>， 5 另一人</code> 是代码示例。',
    '<span title="D: + =1">已知 x=1。</span>',
])
def test_correct_math_html_english_and_code_are_not_blanket_ocr(source):
    assert helper.native_text_quality_reasons(source) == []


def power_geometry(count=2):
    rows=[]
    for i in range(count):
        y=40+i*20
        rows.append({'spans':[
            {'text':'𝐿','size':10,'bbox':(10,y,16,y+10),'flags':4},
            {'text':'2','size':7,'bbox':(16,y-2,20,y+5),'flags':5},
        ]})
    return {'blocks':[{'lines':rows}]}


def test_a_native_baseline_number_is_rejected_only_when_page_proves_a_missing_power():
    source='𝐿² 个，所以 N ≤ 𝐿 2。'
    assert native_script_geometry_reasons(source,power_geometry())
    assert native_script_geometry_reasons(source,{'blocks':[]}) == []
    normal=power_geometry();normal['blocks'][0]['lines'][1]['spans'][1]['flags']=4
    assert native_script_geometry_reasons(source,normal) == []


@pytest.mark.parametrize('source', ['L² + L²', 'L^2 + L^{2}', 'L^{2}，L²'])
def test_complete_equivalent_script_encodings_match_proved_pdf_geometry(source):
    assert native_script_geometry_reasons(source,power_geometry()) == []


@experimental_pdf_only
def test_geometry_wrapper_accepts_retained_html_script_and_protects_literal_metadata():
    page=SimpleNamespace(get_text=lambda *args:power_geometry())
    assert helper._native_script_geometry_reasons('<span>L</span><sup>2</sup> + L²',page) == []
    assert helper._native_script_geometry_reasons("L <sup class='math'>2</sup> + L²",page) == []
    assert helper._native_script_geometry_reasons('代码 `L 2` 与文字 L²。',page) == []


@experimental_pdf_only
def test_new_native_gate_routes_only_damaged_page_and_retains_original_bytes(monkeypatch):
    good='1．已知 y=x²+2x，求最小值。'
    bad='2．已知 b<sub>n+1</sub> = b<sub>n</sub> + ，求通项。'
    inspector=SimpleNamespace(extract_pages_markdown=lambda *args,**kwargs:SimpleNamespace(pages=[
        SimpleNamespace(page=0,markdown=good,needs_ocr=False),SimpleNamespace(page=1,markdown=bad,needs_ocr=False)]))
    monkeypatch.setattr(helper,'_PDF_INSPECTOR_AVAILABLE',True);monkeypatch.setattr(helper,'pdf_inspector',inspector)
    result=helper.inspect_and_extract_pdf(b'synthetic')
    assert result['pages_needing_ocr'] == [1]
    assert result['pages'][1]['markdown'] == bad
    assert result['pages'][0]['quality_reasons'] == []
