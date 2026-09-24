"""Independent safety and real-document fidelity checks for local PDF math.

The optional TeX file is an oracle only. The production repair function receives
only the PDF page; no oracle text, formula list, or expected answer is supplied.
"""

from copy import deepcopy
import builtins
import hashlib
import io
import os
from pathlib import Path
import re

import pytest
import pymupdf as fitz


def math_spans(value):
    """Small independent scanner for this audit's balanced dollar math."""
    spans = []
    cursor = 0
    while cursor < len(value):
        if value[cursor] != '$' or cursor > 0 and value[cursor - 1] == '\\':
            cursor += 1
            continue
        delimiter = '$$' if value.startswith('$$', cursor) else '$'
        end = cursor + len(delimiter)
        while True:
            end = value.find(delimiter, end)
            if end < 0:
                raise AssertionError('Unclosed math delimiter in audit output')
            if end == 0 or value[end - 1] != '\\':
                break
            end += len(delimiter)
        spans.append(value[cursor + len(delimiter):end])
        cursor = end + len(delimiter)
    return spans


def visible_math_key(value):
    """Normalize only visible spelling/sizing for an independent oracle check."""
    value = re.sub(r'\\(?:left|right)\b', '', value)
    value = value.replace(r'\dfrac', r'\frac').replace(r'\ast', '*')
    value = value.replace(r'\mid', '|').replace(r'\vert', '|')
    value = value.replace(r'\not\in', r'\notin').replace(r'\nsubset', r'\not\subset')
    aliases = {'∈': r'\in', '∉': r'\notin', '⊆': r'\subseteq', '⊂': r'\subset',
               '×': r'\times', '−': '-', '∗': '*', '⋅': r'\cdot', '·': r'\cdot'}
    for char, command in aliases.items():
        value = value.replace(char, command)
    # The conditions in the real cases block are Chinese prose; preserve all
    # their characters, commas and periods while allowing different text runs.
    value = re.sub(r'\\text\{([^{}]*)\}', r'\1', value)
    value = re.sub(r'([_^])\{([A-Za-z0-9*])\}', r'\1\2', value)
    value = re.sub(r'(?<![A-Za-z\\])\{([A-Za-z0-9])\}', r'\1', value)
    return re.sub(r'\s+', '', value).replace(r'\not\in', r'\notin')


def complete_math_tape(value):
    return visible_math_key(''.join(math_spans(value)))


def complete_challenge_document_key(value, *, oracle=False):
    """All original text, labels, answers and math; discard layout commands only."""
    if oracle:
        value = value.split(r'\begin{document}', 1)[1].split(r'\end{document}', 1)[0]
        value = re.sub(r'(?m)^\s*%[^\n]*$', '', value)
        ordinal = 0
        def question(_match):
            nonlocal ordinal
            ordinal += 1
            return '题目' + str(ordinal) + '：'
        value = re.sub(r'(?m)^\s*\\item(?![A-Za-z\[])', question, value)
        value = re.sub(r'\\item\[([A-D])\.\]', r'\1.', value)
        value = value.replace(r'\underline{\hspace{2cm}}', r'\fillin')
    else:
        # The PDF's isolated running page number is not part of document text.
        value = re.sub(r'^\s*1\s*\n', '', value, count=1)
        value = re.sub(r'(?m)^#{1,6}\s*', '', value)
        value = re.sub(r'\*\*([^*]+)\*\*', r'\1', value)
    value = re.sub(r'\\(?:begin|end)\{(?:problems|itemize)\}', '', value)
    value = re.sub(r'\\textbf\{([^{}]*)\}', r'\1', value)
    value = re.sub(r'\\(?:quad|qquad)\b', '', value)
    value = value.replace('\\\\', '').replace('$', '')
    return visible_math_key(value)


@pytest.mark.parametrize('changed', [r'$B=A^2$ $30$ $30720$', r'$B=A$ $300$ $30720$', r'$B=A$ $30$ $307200$'])
def test_full_math_tape_does_not_accept_an_expected_formula_as_only_a_prefix(changed):
    assert complete_math_tape(r'$B=A$ $30$ $30720$') != complete_math_tape(changed)


def test_full_math_tape_allows_delimiters_to_split_without_changing_math():
    assert complete_math_tape(r'$B=A$ $x_1+x_2$') == complete_math_tape(r'$B=$ $A$ $x_{1}+$ $x_2$')


@pytest.mark.parametrize('changed', ['题目1：计算$x$。', '题目1：计算$x$。\n答案：D',
                                   '题目1：计算$x$。\n答案：C\n评析：增加未知条件'])
def test_full_document_comparison_does_not_hide_missing_or_changed_plain_answers(changed):
    expected = r'\begin{document}\begin{problems}' + '\n' + r'\item 计算$x$。\textbf{答案：}C' + r'\end{problems}\end{document}'
    assert complete_challenge_document_key(expected, oracle=True) != complete_challenge_document_key(changed)


@pytest.mark.parametrize(('original', 'changed'), [
    (r'x_1+x_4=x_2+x_3', r'x_1+x_3=x_2+x_4'),
    (r'N^*', 'N'), (r'x^2-y^2', r'x^2+y^2'),
    (r'0\notin A', r'0\in A'), (r'B\subseteq M', r'B\subset M'),
    (r'\{x|x=4k+2\}\not\subset M', r'\{x|x=4k+2\}\subset M'),
    (r'\mathbf{N}', r'\mathbb{N}'), ('相同', '不同'),
    ('-7,-3,-1,1,2,3,4,5,6,7,13', '-7,-3,-1,1,2,3,4,5,6,7,12'),
])
def test_independent_oracle_key_keeps_math_critical_differences(original, changed):
    assert visible_math_key(original) != visible_math_key(changed)


def _repair(page):
    from mathbank.pdf_native_math import repair_native_page
    result = repair_native_page(page)
    assert result['status'] in {'repaired', 'unsupported'}
    assert isinstance(result['markdown'], str)
    assert isinstance(result['notes'], list)
    assert isinstance(result['reason'], str)
    if result['status'] == 'unsupported':
        assert result['markdown'] == '', 'Unsafe partial text must not be adopted'
        assert result['reason'], 'Unsupported evidence needs an explicit reason'
    return result


def glyph(char, x, y, font='CMMI10', size=10.5):
    width = size * (0.85 if '\u3400' <= char <= '\u9fff' else 0.5)
    return {'size': size, 'flags': 6 if font.startswith('CMMI') else 4,
            'font': font, 'color': 0, 'ascender': 0.8, 'descender': -0.2,
            'origin': (x, y), 'bbox': (x, y - size * .8, x + width, y + size * .2),
            'chars': [{'origin': (x, y), 'bbox': (x, y - size * .8, x + width, y + size * .2), 'c': char}]}


def row(spans):
    bbox = (min(s['bbox'][0] for s in spans), min(s['bbox'][1] for s in spans),
            max(s['bbox'][2] for s in spans), max(s['bbox'][3] for s in spans))
    return {'wmode': 0, 'dir': (1, 0), 'bbox': bbox, 'spans': spans}


class GlyphPage:
    """Authored rawdict geometry, with no user PDF or TeX content involved."""

    def __init__(self, lines, *, rotation=0, drawings=None):
        self.rect = fitz.Rect(0, 0, 600, 800)
        self.cropbox = self.rect
        self.mediabox = self.rect
        self.rotation = rotation
        self.number = 0
        self._lines = lines
        self._drawings = drawings or []

    def get_text(self, kind='text', **kwargs):
        assert kind == 'rawdict'
        return {'width': 600, 'height': 800,
                'blocks': [{'type': 0, 'number': i, 'bbox': line['bbox'], 'lines': [deepcopy(line)]}
                           for i, line in enumerate(self._lines)]}

    def get_images(self, *args, **kwargs):
        return []

    def get_fonts(self, *args, **kwargs):
        names = sorted({s['font'] for line in self._lines for s in line['spans']})
        return [(i + 1, 'cff', 'Type1', name, 'F' + str(i + 1), 'Identity-H') for i, name in enumerate(names)]

    def get_drawings(self, **kwargs):
        return deepcopy(self._drawings)


def simple_page():
    text = [glyph(c, 40 + i * 10, 60, 'STSong') for i, c in enumerate('题目1：')]
    expression = [glyph('x', 80, 100), glyph('2', 86, 96, 'CMR7', 7),
                  glyph('+', 94, 100, 'CMR10'), glyph('a', 104, 100),
                  glyph('1', 110, 103, 'CMR7', 7), glyph('=', 118, 100, 'CMR10'),
                  glyph('3', 129, 100, 'CMR10')]
    return GlyphPage([row(text), row(expression)])


def test_authored_geometry_binds_scripts_to_their_own_base():
    result = _repair(simple_page())
    assert result['status'] == 'repaired'
    assert 'x^2+a_1=3' in visible_math_key(''.join(math_spans(result['markdown'])))
    assert result['stats']['glyphs_consumed'] == result['stats']['glyphs_total']


@pytest.mark.parametrize('answer', ['C', 'BCD', 'AC'])
def test_plain_letter_answers_are_emitted_not_misclassified_as_page_numbers(answer):
    page = simple_page()
    page._lines.append(row([glyph(char, 40 + index * 10, 140, 'STSong')
                            for index, char in enumerate('答案：' + answer)]))
    result = _repair(page)
    assert result['status'] == 'repaired'
    assert '答案：' + answer in re.sub(r'\s+', '', result['markdown'])


@pytest.mark.parametrize('char', ['\ue123', '\uf8ff', '\ufffd'])
def test_unknown_private_or_replacement_glyph_never_produces_trusted_text(char):
    page = simple_page()
    page._lines[-1] = row([*page._lines[-1]['spans'], glyph(char, 142, 100, 'CMSY10')])
    assert _repair(page)['status'] == 'unsupported'


def test_conflicting_glyphs_at_the_same_position_are_not_silently_deduplicated():
    page = simple_page()
    page._lines[-1] = row([*page._lines[-1]['spans'], glyph('y', 80, 100)])
    assert _repair(page)['status'] == 'unsupported'


def test_exact_duplicate_is_either_rejected_or_consumed_once_with_full_accounting():
    page = simple_page()
    page._lines[-1] = row([*page._lines[-1]['spans'], deepcopy(page._lines[-1]['spans'][0])])
    result = _repair(page)
    if result['status'] == 'repaired':
        assert visible_math_key(''.join(math_spans(result['markdown']))).count('x') == 1
        assert result['stats']['glyphs_consumed'] == result['stats']['glyphs_total']


def test_raised_digit_overlaps_another_baseline_glyph_instead_of_proving_an_exponent():
    page = simple_page()
    spans = page._lines[-1]['spans']
    spans[1] = glyph('2', 104, 96, 'CMR7', 7)  # Directly on top of a, far from x.
    page._lines[-1] = row(spans)
    assert _repair(page)['status'] == 'unsupported'


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_rotated_geometry_does_not_reuse_unrotated_script_and_order_assumptions(rotation):
    page = simple_page()
    page.rotation = rotation
    assert _repair(page)['status'] == 'unsupported'


def test_parallel_question_columns_require_a_safe_fallback():
    page = simple_page()
    right = deepcopy(page._lines)
    for line in right:
        for span in line['spans']:
            span['origin'] = (span['origin'][0] + 300, span['origin'][1])
            span['bbox'] = (span['bbox'][0] + 300, span['bbox'][1], span['bbox'][2] + 300, span['bbox'][3])
            for char in span['chars']:
                char['origin'] = (char['origin'][0] + 300, char['origin'][1])
                char['bbox'] = (char['bbox'][0] + 300, char['bbox'][1], char['bbox'][2] + 300, char['bbox'][3])
        line.update(row(line['spans']))
    page._lines.extend(right)
    assert _repair(page)['status'] == 'unsupported'


def test_parallel_pure_math_fragments_are_not_concatenated_into_one_equation():
    left = [glyph('x', 40, 100), glyph('2', 46, 96, 'CMR7', 7),
            glyph('=', 56, 100, 'CMR10'), glyph('1', 68, 100, 'CMR10')]
    right = [glyph('y', 300, 100), glyph('2', 306, 96, 'CMR7', 7),
             glyph('=', 316, 100, 'CMR10'), glyph('2', 328, 100, 'CMR10')]
    assert _repair(GlyphPage([row(left + right)]))['status'] == 'unsupported'


@pytest.mark.parametrize('symbol', ['√', '∫', '\uf8f1'])
def test_unimplemented_roots_operators_and_incomplete_braces_are_not_flattened(symbol):
    page = simple_page()
    page._lines[-1] = row([*page._lines[-1]['spans'], glyph(symbol, 142, 100, 'CMEX10')])
    assert _repair(page)['status'] == 'unsupported'


@pytest.mark.parametrize('width', [16, 56.7, 120])
def test_fraction_bar_between_math_rows_cannot_be_mistaken_for_a_fill_in_blank(width):
    page = GlyphPage([row([glyph('1', 80, 90, 'CMR10')]), row([glyph('x', 80, 109)])],
                     drawings=[{'type': 's', 'rect': fitz.Rect(76, 96, 76 + width, 96), 'width': .4,
                                'items': [('l', fitz.Point(76, 96), fitz.Point(76 + width, 96))]}])
    assert _repair(page)['status'] == 'unsupported'


@pytest.mark.parametrize(('base', 'overlay', 'left', 'expected'), [
    ('∈', '/', '0', r'0\notin A'),
    ('⊂', '\u0338', 'B', r'B\not\subset A'),
])
def test_distinct_negative_overprints_keep_their_own_base_relation(base, overlay, left, expected):
    page = GlyphPage([row([glyph(left, 80, 100, 'CMR10' if left == '0' else 'CMMI10'),
                          glyph(overlay, 91 if overlay == '/' else 90, 100,
                                'CMR10' if overlay == '/' else 'CMSY10'),
                          glyph(base, 90, 100, 'CMSY10'), glyph('A', 104, 100)])])
    result = _repair(page)
    assert result['status'] == 'repaired'
    assert visible_math_key(expected) in visible_math_key(''.join(math_spans(result['markdown'])))
    assert result['stats']['glyphs_consumed'] == result['stats']['glyphs_total']


def test_negative_overlay_on_an_unsupported_symbol_cannot_become_notin():
    page = GlyphPage([row([glyph('x', 80, 100), glyph('\u0338', 90, 100, 'CMSY10'),
                          glyph('+', 90, 100, 'CMR10'), glyph('A', 104, 100)])])
    assert _repair(page)['status'] == 'unsupported'


def test_nonoverlapping_slash_cannot_be_used_to_negate_a_distant_relation():
    page = GlyphPage([row([glyph('x', 80, 100), glyph('/', 90, 100, 'CMR10'),
                          glyph('∈', 120, 100, 'CMSY10'), glyph('A', 132, 100)])])
    result = _repair(page)
    if result['status'] == 'repaired':
        assert r'\notin' not in result['markdown']
        assert '/' in result['markdown']
        assert r'\in' in result['markdown'] or '∈' in result['markdown']


def test_degree_symbol_is_never_guessed_to_be_digit_zero_or_sentence_punctuation():
    page = GlyphPage([row([glyph('x', 80, 100), glyph('=', 90, 100, 'CMR10'),
                          glyph('3', 101, 100, 'CMR10'), glyph('0', 107, 100, 'CMR10'),
                          glyph('°', 113, 96, 'CMSY7', 7)])])
    result = _repair(page)
    if result['status'] == 'repaired':
        assert r'\circ' in result['markdown'] or '°' in result['markdown']
        assert '30^0' not in visible_math_key(result['markdown'])


def test_raised_dot_is_not_automatically_a_degree_sign():
    page = GlyphPage([row([glyph('x', 80, 100), glyph('=', 90, 100, 'CMR10'),
                          glyph('3', 101, 100, 'CMR10'), glyph('.', 109, 96, 'CMR7', 7)])])
    result = _repair(page)
    if result['status'] == 'repaired':
        assert r'\circ' not in result['markdown'] and '°' not in result['markdown']
        assert '.' in result['markdown']


@pytest.mark.skipif(not (os.environ.get('MATHBANK_NATIVE_MATH_PDF') and os.environ.get('MATHBANK_NATIVE_MATH_ORACLE')),
                    reason='real native PDF plus independent oracle validation is opt-in')
def test_real_pdf_repair_consumes_glyphs_and_preserves_every_oracle_formula(monkeypatch):
    pdf = Path(os.environ['MATHBANK_NATIVE_MATH_PDF'])
    oracle = Path(os.environ['MATHBANK_NATIVE_MATH_ORACLE'])
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    with fitz.open(pdf) as document:
        assert len(document) == 1
        raw = document[0].get_text('rawdict')
        independent_raw_count = sum(len(span.get('chars', [])) for block in raw['blocks']
                                    for line in block.get('lines', []) for span in line.get('spans', []))
        def forbid_tex_read(opener):
            def guarded(file, *args, **kwargs):
                if isinstance(file, (str, os.PathLike)) and str(file).lower().endswith('.tex'):
                    raise AssertionError('The repair function must not read the TeX oracle')
                return opener(file, *args, **kwargs)
            return guarded
        with monkeypatch.context() as protected:
            protected.setattr(builtins, 'open', forbid_tex_read(builtins.open))
            protected.setattr(io, 'open', forbid_tex_read(io.open))
            result = _repair(document[0])  # The oracle is deliberately read only afterward.
    assert result['status'] == 'repaired', result['reason']
    assert result['stats']['glyphs_consumed'] == result['stats']['glyphs_total'] == independent_raw_count
    oracle_text = oracle.read_text()
    expected = math_spans(oracle_text.split(r'\begin{document}', 1)[1])
    actual = visible_math_key(''.join(math_spans(result['markdown'])))
    assert len(expected) == 33
    # Keep order AND every repeated occurrence, not merely a bag of formulas.
    cursor = 0
    for number, formula in enumerate(expected, 1):
        if number == 23:
            assert formula.strip() == r'\cdot'  # Printed provenance separator, not an equation.
            continue
        key = visible_math_key(formula)
        position = actual.find(key, cursor)
        assert position >= 0, f'Oracle formula {number} missing or out of order: {formula}'
        cursor = position + len(key)
    # A subsequence by itself would miss 30→300 or B=A→B=A^2. The entire
    # semantic tape must match, allowing different dollar/span boundaries.
    mathematical_expected = [formula for index, formula in enumerate(expected, 1) if index != 23]
    assert actual in {visible_math_key(''.join(expected)), visible_math_key(''.join(mathematical_expected))}, 'Extra, missing, or changed mathematics in the complete tape'
    # This also checks the provenance dot when emitted in prose, the ordinary
    # choices and C/BCD/AC answers, comments and their question ownership.
    assert complete_challenge_document_key(result['markdown']) == complete_challenge_document_key(oracle_text, oracle=True)
    assert result['markdown'].count(r'\fillin') == 2
    assert '奇偶性相同' in result['markdown'] and '奇偶性不同' in result['markdown']
    assert r'\begin{cases}' in result['markdown'] and r'\end{cases}' in result['markdown']
    assert not any('\ue000' <= char <= '\uf8ff' for char in result['markdown'])
    assert {name: result['stats'][name] for name in ('cases', 'superscripts', 'subscripts', 'negations', 'fillins')} == {
        'cases': 1, 'superscripts': 4, 'subscripts': 30, 'negations': 2, 'fillins': 2}
    from mathbank.content_locks import lock_visible_math, _source_parts
    _, locks = lock_visible_math(result['markdown'], 'native-layout-validation')
    parts = _source_parts(result['markdown'], locks)
    contents = {part.number: part for part in parts if part.field == 'content'}
    answers = {part.number: part for part in parts if part.field == 'answer_markdown'}
    assert list(contents) == list(answers) == [1, 2, 4, 5]
    assert [part.number for part in parts if part.field == 'placeholder'] == [3]
    compact = lambda text: re.sub(r'\s+|\$', '', text)
    assert 'A.6B.8C.15D.16' in compact(contents[1].text)
    for number, answer in ((1, 'C'), (2, 'BCD'), (4, 'AC'), (5, '30；30720')):
        assert compact(answers[number].text).startswith('答案：' + answer)
    assert '下附一道类似的题。' in compact(answers[2].text)
    assert '下附一道类似的一道题' not in compact(answers[2].text)
    assert complete_math_tape(answers[4].text) == visible_math_key(expected[21])
    assert contents[5].text.count(r'\fillin') == 2
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('rotation', [90, 180, 270])
@pytest.mark.skipif(not os.environ.get('MATHBANK_NATIVE_MATH_PDF'), reason='real PDF rotation audit is opt-in')
def test_real_rotated_pdf_uses_fallback_without_mutating_original(rotation):
    pdf = Path(os.environ['MATHBANK_NATIVE_MATH_PDF'])
    original = pdf.read_bytes()
    with fitz.open(stream=original, filetype='pdf') as document:
        document[0].set_rotation(rotation)
        assert _repair(document[0])['status'] == 'unsupported'
    assert pdf.read_bytes() == original
