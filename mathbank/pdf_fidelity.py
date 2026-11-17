"""Local guards for source material that the PDF split model must not rewrite."""

from collections import Counter
import re

from mathbank.content_locks import _formulas, _is_escaped, _source_parts, markdown_literal_ranges

_FRAC = re.compile(r"\\(?:dfrac|tfrac|frac)(?![A-Za-z])[ \t]*")
_HAN = re.compile(r"[\u3400-\u9fff]")
_ITEM = re.compile(r"\\item(?![A-Za-z])")
_CHOICE_ENV = re.compile(r"\\begin\{choices\}(?:\[[^\]\n]*\])?([\s\S]*?)\\end\{choices\}")
_LETTER = re.compile(r"(?m)^[ \t]*([A-D])[.．、][ \t]*")
_QUESTION = re.compile(r"^(\d{1,3})[.．、][ \t]*")
_FORBIDDEN = re.compile(r"\\(?:text|mbox|verb|operatorname)(?![A-Za-z])")
_ANSWER_LINE = re.compile(r"(?m)^[ \t]*【答案】[ \t]*([^\n]+)")
_EXPLANATION = re.compile(r"(?m)^[ \t]*【(?:解析|详解|解答)】")
_TEACHING_NOTES = re.compile(r"(?m)^[ \t]*【(?:标注|知识点|素养|方法)】")


def _group(text, start):
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    for at in range(start, len(text)):
        if _is_escaped(text, at):
            continue
        if text[at] == "{":
            depth += 1
        elif text[at] == "}":
            depth -= 1
            if not depth:
                return at
    return None


def _fraction(text, start):
    command = _FRAC.match(text, start)
    if not command:
        return None
    first = _group(text, command.end())
    if first is None:
        return None
    second = first + 1
    while second < len(text) and text[second] in " \t":
        second += 1
    stop = _group(text, second)
    if stop is None:
        return None
    return command.end() + 1, first, second + 1, stop


def clean_empty_fraction_wrappers(source):
    """Unwrap a complete inner fraction; never infer a missing operand."""
    patches = []
    unresolved = 0
    for span in _formulas(source, literal_ranges=markdown_literal_ranges(source)):
        formula = source[span.start:span.end]
        if _FORBIDDEN.search(formula) or len(formula) > 3000:
            continue
        covered_until = 0
        for match in _FRAC.finditer(formula):
            if match.start() < covered_until or _is_escaped(formula, match.start()):
                continue
            bounds = _fraction(formula, match.start())
            if not bounds:
                continue
            left, right, denominator, end = bounds
            if formula[denominator:end].strip():
                continue
            inner = formula[left:right].strip()
            inner_bounds = _fraction(inner, 0)
            if (inner_bounds and inner_bounds[-1] + 1 == len(inner)
                    and inner[inner_bounds[0]:inner_bounds[1]].strip()
                    and inner[inner_bounds[2]:inner_bounds[3]].strip()
                    and len(list(_FRAC.finditer(inner))) == 1):
                patches.append((span.start + match.start(), span.start + end + 1, inner))
                covered_until = end + 1
            else:
                unresolved += 1
    for start, end, value in reversed(patches):
        source = source[:start] + value + source[end:]
    return source, len(patches), unresolved


def _anchor(text):
    first_line = text.lstrip().partition("\n")[0]
    return "".join(_HAN.findall(first_line[:200]))


def _same_stem(first, second):
    a, b = _anchor(first), _anchor(second)
    # Require an actual common phrase, independent of the model's numbering.
    return min(len(a), len(b)) >= 10 and a[:min(len(a), len(b), 20)] == b[:min(len(a), len(b), 20)]


def _choices(value):
    candidates = list(_CHOICE_ENV.finditer(value))
    if len(candidates) == 1:
        env = candidates[0]
        choices = list(_ITEM.finditer(env[1]))
        if len(choices) == 4:
            return [(env.start(1) + choice.end(),
                     env.start(1) + choices[i+1].start() if i+1 < 4 else env.end(1))
                    for i, choice in enumerate(choices)]
        return None
    choices = list(_LETTER.finditer(value))
    if [m[1] for m in choices] != list("ABCD"):
        return None
    return [(choice.end(), choices[i+1].start() if i+1 < 4 else len(value))
            for i, choice in enumerate(choices)]


def _set_option(value, option, replacement):
    bounds = _choices(value)
    if bounds is None:
        return None
    left, right = bounds[ord(option) - ord("A")]
    if right <= left:
        return None
    return value[:left] + " " + replacement + "\n" + value[right:]


def _physical_question_lines(page):
    lines = []
    for block in page.get_text("dict", flags=0).get("blocks", []):
        for line in block.get("lines", []):
            lines.append((line["bbox"], "".join(span["text"] for span in line["spans"]).strip()))
    result = []
    for box, text in lines:
        head = _QUESTION.match(text)
        if not head:
            continue
        stem = text[head.end():]
        if not stem:
            next_lines = [(b, value) for b, value in lines
                          if 0 <= b[0] - box[2] <= 24 and abs(b[1] - box[1]) <= 2
                          and len(_anchor(value)) >= 10]
            if len(next_lines) == 1:
                stem = next_lines[0][1]
        if len(_anchor(stem)) >= 10:
            result.append((box, int(head[1]), stem))
    return result


def _stroked_cells(page):
    drawings = page.get_drawings()
    if len(drawings) > 4500:
        return []
    return [item[1] for drawing in drawings if drawing["type"] == "s"
            for item in drawing["items"] if item[0] == "re"
            and 12 <= item[1].width <= page.rect.width * .5
            and 8 <= item[1].height <= page.rect.height * .12]


def find_choice_grids(page):
    """Recognize a local ruled grid to the right of a unique physical option."""
    if page.rotation:
        return []
    cells = _stroked_cells(page)
    if not 4 <= len(cells) <= 240:
        return []
    words = page.get_text("words")
    labels = [w for w in words if re.fullmatch(r"[A-D][.．、]", w[4])]
    heads = _physical_question_lines(page)
    result = []
    for word in labels:
        x0, y0, x1, y1, label = word[:5]
        center = (y0 + y1) / 2
        starts = [c for c in cells if 0 <= c.x0-x1 <= 18 and c.y0 <= center <= c.y1]
        if len(starts) != 1:
            continue
        cell = starts[0]
        top = sorted((c for c in cells if abs(c.y0-cell.y0) < 1 and abs(c.y1-cell.y1) < 1
                      and c.x0 >= cell.x0-.5), key=lambda c: c.x0)
        if not 2 <= len(top) <= 12 or any(not -.5 <= b.x0-a.x1 <= 2 for a, b in zip(top, top[1:])):
            continue
        bottom = cell.y1
        row_count = 1
        while row_count < 8:
            below = [[c for c in cells if 0 <= c.y0-bottom <= 2
                      and abs(c.x0-a.x0)<1 and abs(c.x1-a.x1)<1] for a in top]
            if not all(len(row)==1 for row in below):
                break
            next_bottom = below[0][0].y1
            if any(abs(row[0].y1 - next_bottom) >= 1 for row in below):
                break
            bottom = next_bottom
            row_count += 1
        if row_count < 2:
            continue
        owners = sorted((h for h in heads if h[0][1] < y0 and abs(h[0][0]-x0)<42),
                        key=lambda h: h[0][1], reverse=True)
        if not owners or y0 - owners[0][0][1] > page.rect.height*.35:
            continue
        _, number, stem = owners[0]
        box = [cell.x0-.7, cell.y0-.7, top[-1].x1+.7, bottom+.7]
        dimensions = (page.rect.width, page.rect.height)*2
        result.append({"question_number": number, "stem": stem, "option": label[0],
                       "bbox": [round(x * 1000 / d,4) for x,d in zip(box, dimensions)]})
    counts=Counter((x["question_number"],x["option"]) for x in result)
    return [x for x in result if counts[(x["question_number"],x["option"])] == 1]


def protect_choice_grids(layout, document, *, crop, check_cancelled):
    """Bind a physical table crop to the same numbered choice in the transcript."""
    for index, page_state in enumerate(layout["pages"]):
        check_cancelled()
        text = layout["page_texts"][index]
        if not page_state.get("candidates") or not text:
            continue
        page = document[page_state["page_index"]]
        try:
            grids = find_choice_grids(page)
        except (ValueError, TypeError, RuntimeError):
            continue
        if not grids:
            continue
        parts = [part for part in _source_parts(text, []) if part.field == "content"]
        patches = {}
        for grid in grids:
            matches = [part for part in parts if part.number == grid["question_number"]
                       and _same_stem(grid["stem"], part.text)]
            if len(matches) != 1 or _choices(matches[0].text) is None:
                continue
            part = matches[0]
            count = sum(f.get("kind") == "choice_table" for f in page_state["figures"])
            figure_id = f"p{page_state['page_index'] + 1}-grid{count + 1}"
            check_cancelled()
            asset = crop(page, grid["bbox"], figure_id)
            link = f"![选项表格]({asset})"
            part_key = (part.source_start, part.source_end)
            replacement = _set_option(patches.get(part_key, part.text), grid["option"], link)
            if replacement is None:
                continue
            patches[part_key] = replacement
            page_state["figures"].append({"id": figure_id, "page_index": page_state["page_index"],
                "bbox": grid["bbox"], "image_path": asset, "attached": True,
                "kind": "choice_table", "option": grid["option"], "stem": grid["stem"],
                "question_number": grid["question_number"], "review_reasons": []})
        for (left, right), value in sorted(patches.items(), reverse=True):
            text = text[:left] + value + text[right:]
        layout["page_texts"][index] = text


def enforce_source_fields(questions, source, layout):
    """Use uniquely associated explicit source answer blocks and table crops."""
    report = {"answer_blocks_restored": 0, "choice_tables_restored": 0}
    annotated = {}
    for index, part in enumerate(_source_parts(source, [])):
        if part.field == "content":
            annotated[index] = (part, None)
        elif part.field == "answer_markdown" and part.owner in annotated:
            declared = _ANSWER_LINE.match(part.text.lstrip())
            if not declared or not _EXPLANATION.search(part.text, declared.end()):
                continue
            # The whole labelled original block is read-only. Teaching tags
            # and page markers aren't part of the solution.
            note = _TEACHING_NOTES.search(part.text, declared.end())
            value = part.text[:note.start() if note else len(part.text)].strip()
            if len(value) <= 12000 and len(list(_ANSWER_LINE.finditer(value))) == 1:
                annotated[part.owner] = (annotated[part.owner][0], value)
    table_figures = [f for page in layout["pages"] for f in page["figures"] if f.get("kind") == "choice_table"]
    for part, value in annotated.values():
        if value is None:
            continue
        matches = [q for q in questions if _same_stem(part.text,q.get("content") or "")]
        if len(matches) != 1 or sum(_same_stem(other.text,matches[0].get("content") or "")
                                    for other, _ in annotated.values()) != 1:
            continue
        question=matches[0]
        if question.get("answer_markdown") != value:
            question["answer_markdown"]=value
            report["answer_blocks_restored"] += 1
    for item in table_figures:
        matches = [q for q in questions if _same_stem(item["stem"],q.get("content") or "")]
        if len(matches)!=1 or sum(_same_stem(part.text,matches[0].get("content") or "")
                                  for part, _ in annotated.values())!=1:
            continue
        question=matches[0]
        value=question.get("content") or ""
        url=item["image_path"]
        expected=f"![选项表格]({url})"
        updated=_set_option(value,item["option"],expected)
        if updated is not None and updated != value:
            question["content"]=updated
            report["choice_tables_restored"] += 1
    return report
