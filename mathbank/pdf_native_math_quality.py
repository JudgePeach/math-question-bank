"""Conservative evidence of native two-dimensional math loss; never repair text."""
from collections import Counter
import re
import unicodedata

_LETTER = r'A-Za-z\u0370-\u03ff\U0001d400-\U0001d7cb'
_ATOM = '[' + _LETTER + r'0-9)}\]²³⁰¹⁴⁵⁶⁷⁸⁹]'
_SCRIPT = re.compile(r'''<\s*(sup|sub)\b(?:[^<>"']|"[^"]*"|'[^']*')*>\s*([^<>\n]{1,18})\s*</\s*\1\s*>''', re.I)


def script_markup_probe(source):
    """Canonicalize markup only in the diagnostic copy, before literal masking."""
    def replace(match):
        value = match[2].strip()
        if not re.fullmatch('[' + _LETTER + r'0-9+−-]{1,8}', value):
            return match[0]
        return ('^' if match[1].lower() == 'sup' else '_') + '{' + value + '}'
    return _SCRIPT.sub(replace, source)


def native_math_layout_reasons(visible):
    reasons = []
    # Empty binary slots inside a relation cannot be made valid by unrelated
    # prose or by a denominator later on the line. Literal slots are U+FFFC.
    empty = None
    for match in re.finditer(r'[:：=]\s*[+×÷−-]\s*=|=\s+=|=\s*[，。；;]', visible):
        before = visible[max(0,match.start()-80):match.start()]
        after = visible[match.end():match.end()+32].lstrip()
        left = re.search(r'(?:[0-9)}\]²³⁰¹⁴⁵⁶⁷⁸⁹]|(?<![A-Za-z])[' + _LETTER + r'])\s*$', before)
        math_word = re.search(r'正弦定理|余弦定理|函数|方程|概率|椭圆|双曲线', before)
        right = re.match(r'[+-]?\d|[\u0370-\u03ff\U0001d400-\U0001d7cb]|[A-Za-z](?![A-Za-z])|\\(?:frac|sqrt)\b', after)
        if (left or math_word) and (match[0][-1] != '=' or right or re.match(r'[.，。；;]', after)):
            empty = match
            break
    for match in re.finditer(_ATOM + r'\s*[+×÷−-]\s*[，。；;]', visible):
        prefix = re.split(r'[，,。；;\n]', visible[max(0, match.start()-100):match.start()])[-1]
        if '=' in prefix and not re.search(r'\b(?:grade|rating|level)\s*=\s*$', prefix, re.I):
            empty = match
            break
    if re.search(r'(?:概率|斜率|面积|体积|距离|系数|弦长|半径)(?:为|是|等于)\s*[，。；;]', visible):
        empty = True
    if empty:
        reasons.append('原生提取的数学关系中出现空运算项，需对照原页识别。')
    # The source has a numerator-like fragment before a numbered Chinese
    # question while the question's own displayed relation has lost that term.
    for line in visible.splitlines():
        line = re.sub(r'[\^_]\{([^{}]{1,18})\}', r'\1', line)
        match = re.match(r'^\s*([' + _LETTER + r'0-9\s]{1,40})\s+(\d{1,3})[.．、]\s*(?=[\u3400-\u9fff])', line)
        if (match and re.search('[' + _LETTER + ']', match[1]) and re.search(r'\d', match[1])
                and not re.search(r'[A-Za-z]{2,}', match[1]) and re.search(r'[:：=]|(?:长|面积|体积)为', line[match.end():match.end()+90])):
            reasons.append('原生提取的分子或上标碎片漂移到题号之前，需对照原页识别。')
            break
    # Standalone scripts after a comma/operator have no base. Two such slots
    # avoid treating an isolated authored footnote as a mathematical failure.
    orphan = []
    for line in visible.splitlines():
        if re.search(r'[=<>≤≥]|函数|数列|概率|\b(?:tan|sin|cos)\b', line):
            orphan.extend(re.findall(r'(?:[,，=−-]|[A-D][.．])\s*\^\{[' + _LETTER + r'0-9]{1,3}\}\s*(?:[)），,；;。]|$)', line))
    if len(orphan) >= 2:
        reasons.append('原生提取出现多个没有基底的上标或分式碎片，需对照原页识别。')
    if re.search(r'(?:概率|斜率|面积|体积|距离|系数)[^。；\n]{0,24}(?:为|=)\s*\^\{\d{1,3}\}\s*[，,]\s*\d{1,3}\s*[\u3400-\u9fff]', visible):
        reasons.append('原生提取的分式分子与分母跨正文脱离，需对照原页识别。')
    return reasons


def native_script_geometry_reasons(visible, page_dict):
    """Confirm lost powers only with raised small PDF spans and a native gap.

    Counting is used solely to reject incomplete native evidence, never to
    reconstruct or adopt a formula. Correct Unicode/TeX/HTML powers count as
    retained; a baseline product such as ``L 2`` alone is never a failure.
    """
    pairs = Counter()
    spans_seen = 0
    for block in page_dict.get('blocks', []):
        for line in block.get('lines', []):
            previous = None
            for span in line.get('spans', []):
                spans_seen += 1
                if spans_seen > 5000:
                    return []
                text = str(span.get('text') or '').strip()
                if not text:
                    continue
                if previous and int(span.get('flags', 0)) & 1 and re.fullmatch(r'\d{1,2}', text):
                    base_text = ''.join(unicodedata.normalize('NFKC', c) if unicodedata.category(c).startswith('L') else c for c in str(previous.get('text') or '').strip())
                    base = re.search(r'(?<![A-Za-z])([A-Za-z])$', base_text)
                    try:
                        size = float(previous['size']);small = float(span['size']);a = previous['bbox'];b = span['bbox']
                        geometry = .45*size <= small <= .85*size and -.35*size <= b[0]-a[2] <= .4*size and b[3] < a[3]-.15*size
                    except (KeyError, TypeError, ValueError, IndexError):
                        geometry = False
                    if base and geometry:
                        pairs[(base[1],text)] += 1
                previous = span
    normalized = ''.join(unicodedata.normalize('NFKC', c) if unicodedata.category(c).startswith('L') else c for c in visible)
    normalized = re.sub(r'[*_]', '', normalized)
    superscript = str.maketrans('0123456789','⁰¹²³⁴⁵⁶⁷⁸⁹')
    for (base, exponent), count in pairs.items():
        gap = re.search(r'(?<![A-Za-z])'+re.escape(base)+r'\s+'+exponent+r'(?!\d)', normalized)
        if not gap:
            continue
        power = re.compile(r'(?<![A-Za-z])'+re.escape(base)+r'(?:\s*\^\s*(?:\{\s*'+exponent+r'\s*\}|'+exponent+r'(?!\d))|'+exponent.translate(superscript)+')')
        if len(power.findall(normalized)) < count:
            return ['原页上标几何位置与原生文本不一致，幂次被拆成正文数字，需对照原页识别。']
    return []
