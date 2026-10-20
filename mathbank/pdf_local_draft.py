"""Create editable cards by exact source ranges, without a second text model.

Original source is retained. Known headings, page furniture and number prefixes
have explicit removal receipts. Ambiguity remains a local review/unassigned
range; no model or whole-paper regeneration is dispatched from this module.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import re

from mathbank.content_locks import _formulas
from mathbank.question_assets import _mask_literals, markdown_literal_ranges, embedded_question_assets

SCHEMA = "mathbank.pdf-local-draft.v1"
RULES_VERSION = "pdf-local-draft.rules.v3"
_MARKER = re.compile(r"<!-- MATHBANK_PDF_PAGE:\d+ -->")
_NUMBER = re.compile(r"(?:^[ \t]{0,3}|(?<=[。；;])[ \t]*)(?:#{1,6}[ \t]+)?(?:\*\*|__)?([1-9][0-9]{0,2})(?:\\?[.．、])(?:\*\*|__)?(?![0-9])",re.MULTILINE)
_SECTION = re.compile(r"^[一二三四五六七八九十]+[、.．](单项选择题|多项选择题|单选题|多选题|选择题|填空题|解答题|证明题)(.*)$")
_ANSWER = {"答案","参考答案","参考答案与解析","参考答案及解析","答案与解析","答案及解析",
           "数学参考答案","数学参考答案及多维细目表","参考答案及评分标准","数学参考答案及评分标准"}
_APPENDIX = {"多维细目表","双向细目表","试卷双向细目表","命题细目表"}
_ENV = re.compile(r"\\(begin|end)\s*\{([^{}\n]+)\}")
_ATOMIC = {"choices","choices*","tabular","tabular*","tabularx","longtable","tblr","longtblr","talltblr",
           "array","cases","matrix","pmatrix","bmatrix","align","align*","aligned","gather","gather*",
           "equation","equation*","multline","multline*","tikzpicture","enumerate","itemize"}
_FOOTER_PREFIX = r"(?P<label>数学(?:[（(]模拟卷[ⅠⅡⅢIViv0-9一二三]*[）)])?(?:参考答案)?|参考答案)?"
_FOOTER = re.compile(_FOOTER_PREFIX+r"第(?P<page>[1-9][0-9]{0,3})页(?:共|[/／]共?)(?P<total>[1-9][0-9]{0,3})页")
_SPLIT_FOOTER = re.compile(_FOOTER_PREFIX+r"第(?P<page>[1-9][0-9]{0,3})页[,，](?P<total>[1-9][0-9]{0,3})页")
_WATERMARK = re.compile(r"小红书(?:号\s*[:：]\s*\d+)?")


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _title(line):
    return re.sub(r"\s+","",line.strip(" #*_\t\r\n"))


def _blank(chars,start,end):
    for index in range(start,end):
        if chars[index] not in "\r\n":chars[index]=" "


def _atomic_scan(source):
    chars=list(_mask_literals(source,tex_comments=False));issues=[];atomic=[]
    for a,b in markdown_literal_ranges(source):_blank(chars,a,b)
    initial="".join(chars);stack=[];start=None
    for token in _ENV.finditer(initial):
        action,env=token[1],token[2].strip()
        if action=="begin":
            stack.append((env,token.start()))
            if start is None and env in _ATOMIC:start=token.start()
        elif stack and stack[-1][0]==env:
            stack.pop()
            if start is not None and not any(name in _ATOMIC for name,_ in stack):
                atomic.append([start,token.end()]);_blank(chars,start,token.end());start=None
        else:issues.append({"code":"unbalanced_environment","range":[token.start(),token.end()]})
    if stack:
        begin=start if start is not None else stack[0][1]
        issues.append({"code":"unclosed_environment","range":[begin,len(source)]})
        _blank(chars,begin,len(source))
    return "".join(chars),issues,atomic


def _math_view(view,boundaries=()):
    chars=list(view);offsets=sorted({0,len(view),*boundaries})
    for start,end in zip(offsets,offsets[1:]):
        for formula in _formulas(view[start:end]):_blank(chars,start+formula.start,start+formula.end)
    return "".join(chars)


def _scan(source):
    view,issues,atomic=_atomic_scan(source)
    return _math_view(view),issues,atomic


def _escaped(text,index):
    cursor=index-1
    while cursor>=0 and text[cursor]=="\\":cursor-=1
    return (index-cursor-1)%2==1


def _structural_probe(view):
    """Keep inline-dollar phase errors independent of structural evidence.

    Explicit display/parenthesized math and complete standalone-dollar blocks
    remain opaque. A stray inline dollar cannot hide the following document.
    """
    explicit=list(view)
    for match in re.finditer(r"\$+",view):
        if len(match[0])==1 and not _escaped(view,match.start()):explicit[match.start()]=" "
    chars=list(view)
    for formula in _formulas("".join(explicit)):_blank(chars,formula.start,formula.end)
    for formula in _formulas(view):
        if not formula.formula.startswith("$") or formula.formula.startswith("$$"):continue
        left=view[view.rfind("\n",0,formula.start)+1:formula.start]
        end=view.find("\n",formula.end);end=len(view) if end<0 else end
        if not left.strip() and not view[formula.end:end].strip():_blank(chars,formula.start,formula.end)
    return "".join(chars)


def _declaration_count(line):
    match=re.search(r"本题共\$?([0-9]{1,3}|[一二三四五六七八九十两]{1,3})\$?小题",_title(line))
    if not match:return None
    value=match[1]
    if value.isdigit():return int(value)
    digits={letter:n for n,letter in enumerate("零一二三四五六七八九")};digits["两"]=2
    if value=="十":return 10
    if "十" in value:
        left,right=value.split("十",1)
        return digits.get(left,1)*10+digits.get(right,0)
    return digits.get(value)


def _structure_census(view):
    lines=_lines(view)
    sections=[(a,b,line) for a,b,line in lines if _SECTION.fullmatch(_title(line))]
    answers=[(a,b) for a,b,line in lines if _title(line) in _ANSWER]
    first=sections[0][0] if sections else len(view)
    end=answers[0][0] if answers else len(view)
    sections=[row for row in sections if row[0]<end]
    heads=list(_NUMBER.finditer(view,first,end));numbers=[int(m[1]) for m in heads]
    counts=[_declaration_count(line) for _a,_b,line in sections]
    declared=sum(counts) if counts and all(n is not None and n>0 for n in counts) else None
    section_proof=declared is not None and numbers==list(range(1,declared+1))
    for index,(a,_b,_line) in enumerate(sections):
        stop=sections[index+1][0] if index+1<len(sections) else end
        section_proof=section_proof and sum(a<=m.start()<stop for m in heads)==counts[index]
    answer_numbers=[]
    if len(answers)==1:
        appendix=[a for a,_b,line in lines if a>end and _title(line) in _APPENDIX]
        answer_end=min(appendix) if appendix else len(view)
        answer_numbers=[int(m[1]) for m in _NUMBER.finditer(view,answers[0][1],answer_end)
            if not _summary_line(view,view,m,answer_end)]
    answer_proof=bool(numbers and numbers==list(range(1,len(numbers)+1)) and answer_numbers==numbers)
    return {"sections":sections,"answers":answers,"heads":heads,"numbers":numbers,
        "declared_count":declared,"proved":bool(sections and (section_proof or answer_proof))}


def _scan_structure(source,pages):
    base,issues,atomic=_atomic_scan(source);probe=_structural_probe(base)
    furniture=_page_furniture(source,pages,probe)
    chars=list(base);probe_chars=list(probe)
    for row in furniture:
        _blank(chars,row["start"],row["end"]);_blank(probe_chars,row["start"],row["end"])
    base="".join(chars);probe="".join(probe_chars)
    raw=_structure_census(probe);view=_math_view(base);masked=_structure_census(view)
    notices=[];recovered=False
    differs=(raw["numbers"]!=masked["numbers"] or raw["answers"]!=masked["answers"])
    if differs and raw["proved"]:
        boundaries={m.start() for m in raw["heads"]}
        boundaries.update(a for a,_b,_line in raw["sections"])
        boundaries.update(a for a,_b in raw["answers"])
        view=_math_view(base,boundaries);recovered=True
        # A remaining dollar is a source warning, never a deletion or a claim
        # that a formula was checked against the PDF. Keep the exact source.
        for match in re.finditer(r"\$",view):
            if not _escaped(view,match.start()):
                notices.append({"code":"source_math_delimiter_requires_review","range":[match.start(),match.end()]})
    actual=_structure_census(view)
    expected=raw["declared_count"]
    if expected is not None and actual["numbers"]!=list(range(1,expected+1)):
        first=raw["sections"][0][0] if raw["sections"] else 0
        stop=raw["answers"][0][0] if raw["answers"] else len(source)
        issues.append({"code":"source_structure_census_requires_review","range":[first,stop]})
    elif differs and not raw["proved"]:
        # Without a proved census do not promote hidden prose into questions.
        # But an inline shell crossing a paragraph and an explicit prose head
        # cannot be reported as a complete successful split either.
        for head in raw["heads"]:
            if any(m.start()==head.start() for m in actual["heads"]):continue
            if not re.match(r"\s*[\u3400-\u9fff]",probe[head.end():]):continue
            for formula in _formulas(base):
                if formula.start<head.start()<formula.end and re.search(r"\n\s*\n",base[formula.start:head.start()]):
                    issues.append({"code":"source_structure_census_requires_review",
                        "range":[formula.start,formula.end]})
                    break
    diagnostic={"declared_count":expected,"independent_numbers":raw["numbers"],
        "masked_numbers":masked["numbers"],"final_numbers":actual["numbers"],
        "independent_boundaries_proved":raw["proved"],"recovered_boundaries":recovered}
    return view,issues,atomic,furniture,notices,diagnostic


def _lines(text):
    result=[];offset=0
    for line in text.splitlines(keepends=True):
        result.append((offset,offset+len(line),line));offset+=len(line)
    return result


def _page_ranges(source,pages):
    if not isinstance(pages,list) or not pages:return []
    if any(not isinstance(page,dict) or type(page.get("page_number")) is not int or page["page_number"]<1
           or not isinstance(page.get("markdown"),str) for page in pages):return []
    numbers=[p["page_number"] for p in pages]
    if numbers!=sorted(set(numbers)):return []
    chunks=[_MARKER.sub("",p["markdown"].strip()) for p in pages]
    if "\n\n".join(chunks)!=source:
        chunks=[chunk.strip() for chunk in chunks]
        if "\n\n".join(chunks)!=source:return []
    result=[];offset=0
    for page,chunk in zip(pages,chunks):
        result.append({"page_number":page["page_number"],"range":[offset,offset+len(chunk)]});offset+=len(chunk)+2
    return result


def _page_furniture(source,pages,view):
    """Prove terminal page counters before interpreting any source headings.

    A small suffix of a *physical* page must consist entirely of a known label
    and a complete counter, or the explicit OCR form ``第3页 / , / 5页``.
    An isolated answer heading is never enough. Protected math, code and TeX
    environments do not participate, even when they span physical pages.
    """
    answer_titles=[a for a,_b,line in _lines(view) if _title(line) in _ANSWER]
    found=[]
    for page in pages:
        begin,end=page["range"]
        rows=[(begin+a,begin+b,line) for a,b,line in _lines(source[begin:end]) if line.strip()]
        terminal=list(rows);watermarks=[]
        while terminal and len(watermarks)<2 and _WATERMARK.fullmatch(terminal[-1][2].strip()):
            watermarks.insert(0,terminal.pop())
        tail=terminal[-6:]
        for position in range(len(tail)):
            candidate=tail[position:];left,right=candidate[0][0],candidate[-1][1]
            # Keep the evidence local, without swallowing long blank passages.
            if source[left:right].count("\n")>16 or source[left:right]!=view[left:right]:continue
            compact="".join(_title(line) for _a,_b,line in candidate)
            match=_FOOTER.fullmatch(compact);evidence="complete_page_counter"
            if match is None and len(candidate)>1:
                match=_SPLIT_FOOTER.fullmatch(compact);evidence="split_page_counter"
                if match is not None and not (match["label"] or "").endswith("参考答案"):continue
            if match is None or int(match["page"])>int(match["total"]):continue
            label=match["label"] or ""
            # A first standalone answer heading followed by a page counter may
            # start a real answer section at the page break. Preserve it. A
            # subsequent terminal label is furniture only with counter proof.
            standalone_answer_label=any(_title(line) in _ANSWER for _a,_b,line in candidate)
            if standalone_answer_label and label.endswith("参考答案") and not any(a<left for a in answer_titles):
                continue
            found.append({"start":left,"end":right,"reason":"page_footer",
                "page_number":page["page_number"],"evidence":evidence,"text":source[left:right]})
            for a,b,_line in watermarks:
                if source[a:b]==view[a:b]:
                    found.append({"start":a,"end":b,"reason":"page_watermark",
                        "page_number":page["page_number"],"evidence":"follows_page_counter","text":source[a:b]})
            break
    return found


def _subtract(bounds,removals):
    start,end=bounds;pieces=[];cursor=start
    for left,right,_reason in sorted(removals):
        left,right=max(start,left),min(end,right)
        if left>=right:continue
        if cursor<left:pieces.append([cursor,left])
        cursor=max(cursor,right)
    if cursor<end:pieces.append([cursor,end])
    return pieces


def _copy(source,bounds,removals):
    spans=_subtract(bounds,removals)
    # Trim only actual surrounding whitespace, recording its exact source.
    while spans and not source[slice(*spans[0])].strip():spans.pop(0)
    while spans and not source[slice(*spans[-1])].strip():spans.pop()
    if spans:
        a,b=spans[0];spans[0][0]=a+len(source[a:b])-len(source[a:b].lstrip())
        a,b=spans[-1];spans[-1][1]=b-(len(source[a:b])-len(source[a:b].rstrip()))
    return "".join(source[slice(*span)] for span in spans),spans


def _receipts(source,bounds,kept,known):
    if not bounds:return []
    cursor,end=bounds;result=[]
    for left,right in [*sorted(kept),[end,end]]:
        if cursor<left:
            reasons=sorted({reason for a,b,reason in known if a<left and b>cursor})
            result.append({"start":cursor,"end":left,"reason":reasons[0] if len(reasons)==1 else
                "source_furniture" if reasons else "surrounding_whitespace","reasons":reasons,
                "text":source[cursor:left]})
        cursor=max(cursor,right)
    return result


def _summary_line(source,view,match,end):
    line_end=source.find("\n",match.start(),end);line_end=end if line_end<0 else line_end
    fragment=source[match.start():line_end]
    labels=list(re.finditer(r"(?<![A-Za-z0-9])([1-9][0-9]{0,2})\\?[.．][ \t]*",fragment))
    if len(labels)<2:return False
    values=[fragment[m.end():labels[i+1].start() if i+1<len(labels) else len(fragment)] for i,m in enumerate(labels)]
    return all(value.strip() and len(value)<120 and not re.search(r"[\u3400-\u9fff]",value.replace("或","")) for value in values)


def _table_answers(source,atomic,begin,end,allowed):
    """Only simple explicit 题号/答案 column pairs; nested tables stay intact."""
    found=defaultdict(list)
    for left,right in atomic:
        if not begin<=left<right<=end:continue
        text=source[left:right]
        header=re.match(r"\\begin\{tabular\}(?:\{[^{}]*\})",text)
        if not header or text.count(r"\begin{tabular}")!=1:continue
        rows=[];cells=[];cursor=left+header.end();cell=cursor;depth=0;index=cursor
        maths=[(left+f.start,left+f.end) for f in _formulas(text)]
        while index<right:
            math=next((b for a,b in maths if a<=index<b),None)
            if math is not None:index=math;continue
            char=source[index]
            if char=="{" and (index==0 or source[index-1]!="\\"):depth+=1
            elif char=="}" and (index==0 or source[index-1]!="\\"):depth=max(0,depth-1)
            elif not depth and char=="&":cells.append([cell,index]);cell=index+1
            elif not depth and source.startswith("\\\\",index):
                cells.append([cell,index]);rows.append(cells);cells=[]
                while index<right and source[index]=="\\":index+=1
                cell=index;continue
            index+=1
        cleaned=[]
        for row in rows:
            values=[]
            for a,b in row:
                raw=source[a:b];prefix=re.match(r"(?:\s|\\hline|\\cline\{[0-9-]+\})*",raw).end()
                a+=prefix
                while a<b and source[a].isspace():a+=1
                while b>a and source[b-1].isspace():b-=1
                values.append((source[a:b],[a,b]))
            cleaned.append(values)
        for position,row in enumerate(cleaned[:-1]):
            if not row or _title(row[0][0])!="题号":continue
            answers=cleaned[position+1]
            if len(answers)!=len(row) or _title(answers[0][0])!="答案":continue
            for (_,number_range),(answer,answer_range) in zip(row[1:],answers[1:]):
                number=source[slice(*number_range)].strip(" $\t\r\n")
                if number.isdigit() and int(number) in allowed and answer:
                    found[int(number)].append(answer_range)
    return found


def prepare_pdf_local_draft(source,source_pages,layout_result=None,source_document_sha256=None):
    """Persist-ready exact cards and all remaining source, with no AI calls."""
    if not isinstance(source,str) or not source.strip() or len(source)>500000:
        raise ValueError("PDF 本地原文为空或超过保存上限。")
    source_sha=_sha(source);pages=_page_ranges(source,source_pages)
    plan={"schema":SCHEMA,"rules_version":RULES_VERSION,"input_sha256":source_sha,"source_sha256":source_sha,
        "document_sha256":source_document_sha256,"source":source,"ready":False,"partial":True,
        "questions":[],"items":[],"source_matches":[],"preserved_indices":[],"unassigned":[],
        "manual_reasons":[],"page_issues":[],"page_furniture":[],
        "source_basis":"first_pass_transcription","original_verified":False}
    if not pages:
        plan["manual_reasons"]=["source_page_binding_unproved"]
        plan["unassigned"]=[{"range":[0,len(source)],"role":"manual","reason":"source_page_binding_unproved","text":source}]
        return plan
    view,issues,atomic,furniture,math_notices,census=_scan_structure(source,pages)
    plan["page_furniture"]=furniture;plan["structure_census"]=census
    for page in pages:
        a,b=page["range"]
        codes=list(dict.fromkeys(row["code"] for row in [*issues,*math_notices]
            if row["range"][0]<b and row["range"][1]>a))
        if codes:plan["page_issues"].append({"page_number":page["page_number"],"reason_codes":codes})
    lines=_lines(view)
    sections=[(a,b,_SECTION.fullmatch(_title(line))) for a,b,line in lines if _SECTION.fullmatch(_title(line))]
    answers=[(a,b) for a,b,line in lines if _title(line) in _ANSWER]
    if not sections:
        reason="question_section_missing"
        plan["manual_reasons"]=[reason];plan["unassigned"]=[{"range":[0,len(source)],"role":"manual","reason":reason,"text":source}]
        return plan
    first=sections[0][0];answer_start=answers[0][0] if answers else len(source)
    if answer_start<first:
        # A heading before any declared question section does not prove where
        # the question-only region ends. Do not reinterpret numbered answers.
        reason="question_region_unproved"
        plan["manual_reasons"]=[reason]
        plan["unassigned"]=[{"range":[0,len(source)],"role":"manual","reason":reason,"text":source}]
        return plan
    ambiguous_answers=len(answers)>1
    sections=[row for row in sections if row[0]<answer_start]
    appendix=[a for a,b,line in lines if a>answer_start and _title(line) in _APPENDIX]
    answer_end=min(appendix) if appendix else len(source)
    heads=list(_NUMBER.finditer(view,first,answer_start));numbers=[int(m[1]) for m in heads]
    counts={number:numbers.count(number) for number in numbers}
    if not heads:
        plan["manual_reasons"]=["question_numbers_unproved"]
        plan["unassigned"]=[{"range":[0,len(source)],"role":"manual","reason":"question_numbers_unproved","text":source}]
        return plan
    removals=[(row["start"],row["end"],row["reason"]) for row in furniture]
    # Remove a repeated, declared cover title only at physical page tops.
    prefix=[_title(line) for a,b,line in lines if a<first and line.strip()]
    declared={value for value in prefix if len(value)>8 and ("考试" in value or "练习" in value or "试卷" in value)}
    declared.update(prefix[i]+prefix[i+1] for i in range(max(0,len(prefix)-1)) if prefix[i] in declared)
    for page in pages:
        a,b=page["range"];top=[line for line in _lines(source[a:b]) if line[2].strip()][:3]
        for left,right,line in top:
            if _title(line) in declared and source[a+left:a+right]==view[a+left:a+right]:
                removals.append((a+left,a+right,"repeated_page_header"))
    for a,b,match in sections:removals.append((a,b,"question_section_heading"))
    contexts={}
    for position,(a,b,_match) in enumerate(sections):
        boundary=sections[position+1][0] if position+1<len(sections) else answer_start
        first_head=next((head for head in heads if b<=head.start()<min(boundary,answer_start)),None)
        if first_head is None:continue
        context,context_ranges=_copy(source,[b,first_head.start()],removals)
        if context:
            from mathbank.source_metadata import _section_declaration
            if _section_declaration("一、选择题："+context) is not None:context="";context_ranges=[]
        contexts[a]=(context,context_ranges)
        # This section's introductory material belongs to its following
        # questions, never to the previous section's last question.
        removals.append((b,first_head.start(),"following_section_context"))
    # Exact-role answer/appendix titles and known page furniture are context,
    # never mathematical content to be appended to the preceding card.
    detail=[] if ambiguous_answers else [m for m in _NUMBER.finditer(view,answers[0][1] if answers else answer_start,answer_end)
            if not _summary_line(source,view,m,answer_end)]
    detailed=defaultdict(list)
    for i,match in enumerate(detail):
        number=int(match[1]);stop=detail[i+1].start() if i+1<len(detail) else answer_end
        if number in counts:detailed[number].append((match,stop))
    summary=defaultdict(list) if ambiguous_answers else _table_answers(source,atomic,answer_start,answer_end,set(numbers))
    manual=[]
    if ambiguous_answers:
        manual.append("answer_section_ambiguous")
        plan["unassigned"].append({"range":[answer_start,answer_end],"role":"manual",
            "reason":"answer_section_ambiguous","text":source[answer_start:answer_end]})
        for page in pages:
            a,b=page["range"]
            if any(a<=left<b for left,_right in answers):
                issue=next((row for row in plan["page_issues"] if row["page_number"]==page["page_number"]),None)
                if issue is not None:issue["reason_codes"].append("answer_section_ambiguous")
                else:plan["page_issues"].append({"page_number":page["page_number"],"reason_codes":["answer_section_ambiguous"]})
    if issues:
        manual.extend(issue["code"] for issue in issues)
        for issue in issues:
            left,right=issue["range"]
            plan["unassigned"].append({"range":[left,right],"role":"manual",
                "reason":issue["code"],"text":source[left:right]})
    for index,match in enumerate(detail):
        if int(match[1]) in counts:continue
        stop=detail[index+1].start() if index+1<len(detail) else answer_end
        plan["unassigned"].append({"range":[match.start(),stop],"role":"manual",
            "source_number":int(match[1]),"reason":"original_answer_without_question",
            "text":source[match.start():stop]})
        manual.append("original_answer_without_question")
    if sorted(set(numbers))!=list(range(1,max(numbers)+1)) or numbers!=sorted(set(numbers)):
        manual.append("question_number_census_requires_review")
    if not answers:plan["warnings"]=["original_answers_not_present"]
    for i,head in enumerate(heads):
        number=int(head[1]);stop=heads[i+1].start() if i+1<len(heads) else answer_start
        if counts[number]!=1:
            plan["unassigned"].append({"range":[head.start(),stop],"role":"manual","reason":"duplicate_question_number","text":source[head.start():stop]})
            continue
        local_removals=[*removals,(head.start(),head.end(),"source_number_prefix")]
        content,stem_ranges=_copy(source,[head.start(),stop],local_removals)
        section=max((row for row in sections if row[0]<=head.start()),key=lambda row:row[0])
        section_text=source[section[0]:section[1]].strip();kind=section[2][1]
        context,context_ranges=contexts.get(section[0],("",[]))
        if context:
            content=context+"\n\n"+content
        question_type=("multi_choice" if "多项" in kind or "多选" in kind or "多项符合" in section_text else
            "single_choice" if "单项" in kind or "单选" in kind or "只有一项" in section_text else
            "fill_in_blank" if "填空" in kind else "detailed_answer" if kind in {"解答题","证明题"} else "")
        reasons=[];source_warnings=[];answer="";answer_ranges=[];raw_answer="";answer_bounds=None
        if ambiguous_answers:
            reasons.append("answer_section_ambiguous")
        elif len(detailed[number])==1:
            match,end=detailed[number][0];answer_removals=[*removals,(match.start(),match.end(),"answer_number_prefix")]
            answer,answer_ranges=_copy(source,[match.start(),end],answer_removals);raw_answer=source[match.start():end]
            answer_bounds=[match.start(),end]
            local_removals.append((match.start(),match.end(),"answer_number_prefix"))
        elif len(detailed[number])>1:
            reasons.append("original_answer_ambiguous")
            for match,end in detailed[number]:plan["unassigned"].append({"range":[match.start(),end],
                "role":"manual","source_number":number,"reason":"original_answer_ambiguous","text":source[match.start():end]})
        elif len(summary[number])==1:
            answer_ranges=[summary[number][0]];answer=source[slice(*answer_ranges[0])];raw_answer=answer;answer_bounds=answer_ranges[0]
        elif answers:reasons.append("original_answer_unassigned")
        if any(a<stop and b>head.start() for a,b in [issue["range"] for issue in issues]):reasons.append("source_structure_requires_review")
        for notice in math_notices:
            a,b=notice["range"]
            if (a<stop and b>head.start()) or (answer_bounds and a<answer_bounds[1] and b>answer_bounds[0]):
                source_warnings.append(notice["code"])
        source_warnings=list(dict.fromkeys(source_warnings))
        if not content.strip():reasons.append("empty_question_content")
        if re.search(r"\[(?:公式|特殊字符|插图)[^\]\n]*(?:待核对|待补)",content+answer):source_warnings.append("source_contains_review_marker")
        identity=source_document_sha256 or source_sha
        identifier="PLD_"+_sha(json.dumps([identity,number],separators=(",",":")))[:24]
        q={"source_number":number,"content":content,"answer_markdown":answer,"question_type":question_type,
           "category_compulsory":"","category_chapter":"","category_knowledge":"","difficulty":"normal",
           "source":"","referenced_images":embedded_question_assets(content,answer),"section_context":section_text,
           "shared_context":context,"source_review":{"required":bool(reasons or source_warnings),"status":"not_verified",
               "reasons":list(reasons+source_warnings),"source_basis":"first_pass_transcription","original_verified":False}}
        item={"id":identifier,"source_number":number,"content":content,"answer_markdown":answer,"question":deepcopy(q),
            "stem_ranges":stem_ranges,"answer_ranges":answer_ranges,"context_ranges":deepcopy(context_ranges),
            "raw_stem_range":[head.start(),stop],"raw_answer_range":answer_bounds,
            "raw_content":source[head.start():stop],"raw_answer":raw_answer,"manual_reasons":reasons,
            "source_warnings":source_warnings,
            "insertions":[{"offset":len(context),"text":"\n\n","reason":"shared_context_join"}] if context else [],
            "removals":_receipts(source,[head.start(),stop],stem_ranges,local_removals)
                +_receipts(source,answer_bounds,answer_ranges,local_removals)}
        index=len(plan["questions"]);plan["questions"].append(q);plan["items"].append(item)
        for field,ranges in (("content",stem_ranges),("answer_markdown",answer_ranges)):
            if ranges:plan["source_matches"].append({"question_index":index,"field":field,"source_number":number,
                "source_start":min(r[0] for r in ranges),"source_end":max(r[1] for r in ranges),
                "source_ranges":deepcopy(ranges),"source_excerpt":"".join(source[slice(*r)] for r in ranges),
                "source_basis":"first_pass_transcription","original_verified":False})
    # Everything outside original stem/answer roles remains inspectable; known
    # cover/answer tables/appendix material does not make a complete draft partial.
    plan["unassigned"].insert(0,{"range":[0,heads[0].start()],"role":"document_context","reason":"cover_and_instructions","text":source[:heads[0].start()]})
    if answers and not ambiguous_answers:
        first_answer=min((m.start() for m in detail),default=answer_end)
        plan["unassigned"].append({"range":[answer_start,first_answer],"role":"answer_context","reason":"answer_heading_or_summary","text":source[answer_start:first_answer]})
    if answer_end<len(source):plan["unassigned"].append({"range":[answer_end,len(source)],"role":"appendix","reason":"declared_blueprint","text":source[answer_end:]})
    plan["manual_reasons"]=list(dict.fromkeys(manual));plan["ready"]=bool(plan["questions"])
    plan["partial"]=bool(manual or any(item["manual_reasons"] for item in plan["items"]) or any(x["role"]=="manual" for x in plan["unassigned"]))
    plan["preserved_indices"]=list(range(len(plan["questions"])))
    return plan
