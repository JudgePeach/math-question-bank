import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict
from io import BytesIO
from sqlalchemy.orm import Session
from mathbank.database import Question, Paper, PaperQuestion, QuestionCurriculum
from mathbank.paths import TEMPLATES_DIR

# In-memory LRU cache for compiled PDF bytes
_PDF_CACHE_LOCK = threading.Lock()
_PDF_CACHE = OrderedDict()  # key -> (pdf_bytes, log_or_err)
_MAX_PDF_CACHE_SIZE = 20

# Constants for question type labels
TYPE_LABELS = {
    "single_choice": "单项选择题",
    "multi_choice": "多项选择题",
    "fill_in_blank": "填空题",
    "detailed_answer": "解答题"
}

TYPE_ORDER = ["single_choice", "multi_choice", "fill_in_blank", "detailed_answer"]

def clean_choice_stem_parentheses(text: str) -> str:
    """自动清洗选择题题干末尾供填答用的全角/半角空括号（支持包裹全角空格\u3000、$公式符号、\\quad等），并保持 $ 符号闭合平衡"""
    if not text:
        return ""
    text = text.strip()
    pattern = r'(?:[\s\xa0\u3000]*[\(（]\s*\$?\s*(?:\\quad|\\qquad|\\hspace\{.*?\}|[\s\xa0\u3000_])*?\s*\$?\s*[\)）]\s*\$?[\s\xa0\u3000]*)+$'
    cleaned = re.sub(pattern, '', text).strip()
    cleaned = re.sub(r'\\paren\b', '', cleaned).strip()
    
    # 校验 $ 闭合奇偶性，若因抹除括号导致 $ 符号不成对（奇数个），在末尾补全闭合 $
    dollars = re.findall(r'(?<!\\)\$', cleaned)
    if len(dollars) % 2 != 0:
        cleaned += "$"
        
    return cleaned

def format_stem_paragraphs(stem_text: str) -> str:
    r"""
    Safely assemble question stem lines into coherent LaTeX paragraphs.
    1. Preserves explicit double line breaks (empty lines) intended by the user.
    2. Identifies sub-question & note starts ONLY at line start (e.g. (1), (2), (a), (b), ①, ②, 注：, 提示：),
       placing each into a separate LaTeX paragraph with standard 2em indentation.
    3. Keeps block LaTeX environments (e.g. \begin{...}, \end{...}, $$) intact without inline space merging.
    4. Recombines single line breaks inside the same paragraph into a space for continuous TeX wrapping.
    """
    if not stem_text:
        return ""
        
    lines_list = stem_text.split('\n')
    paragraphs = []
    current_para = []

    for line in lines_list:
        line_str = line.strip()
        
        # 遇到显式空行，代表原作者故意留下的独立段落，直接收尾当前段落
        if not line_str:
            if current_para:
                paragraphs.append(" ".join(current_para))
                current_para = []
            continue
            
        # 判定是否为小问或独立提示/注记开头
        is_sub_start = bool(re.match(r'^([（(]?[0-9a-zA-Z一二三四五六七八九十]+[）\.\)]|①|②|③|④|⑤|【|注[:：]|提示[:：])', line_str))
        # 判定是否为独立块级 TeX 环境
        is_block_env = bool(re.match(r'^(\$\||\\begin\{|\\end\{)', line_str))
        
        if (is_sub_start or is_block_env) and current_para:
            paragraphs.append(" ".join(current_para))
            current_para = [line_str]
        else:
            current_para.append(line_str)

    if current_para:
        paragraphs.append(" ".join(current_para))

    return "\n\n".join(paragraphs)

def clean_content_for_latex(content: str, q_type: str = "", is_answer: bool = False) -> str:
    r"""
    Clean markdown/LaTeX question content for exam-zh LaTeX document export based on 试卷类模板.tex.
    Preserves math delimiters $...$ and $$...$$, tikz code, and handles choices & \paren environments.
    """
    if not content:
        return ""
    
    text = content.strip()
    
    # 如果是选择题，先清洗题干末尾残留的全角/半角供填答空括号，避免与右侧 \paren 生成括号重叠
    if q_type in ["single_choice", "multi_choice"] or r"\begin{choices}" in text or re.search(r'^\s*[-*]?\s*[A-D][\.、\s]', text, re.MULTILINE):
        text = clean_choice_stem_parentheses(text)
    
    # If TikZ code is present in text, remove ANY markdown image tags ![](...) to avoid duplicate image rendering
    if r"\begin{tikzpicture}" in text:
        text = re.sub(r'!\[.*?\]\([^)]+\)', '', text)

    # Convert Markdown images ![](/static/uploads/xxx.png) or ![](uploads/xxx.png) to \includegraphics{...}
    def replace_img(match):
        img_path = match.group(1) or match.group(2)
        base_name = os.path.basename(img_path)
        return f"\n\\begin{{center}}\n\\includegraphics[max width=0.85\\linewidth]{{{base_name}}}\n\\end{{center}}\n"
    
    text = re.sub(r'!\[.*?\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)', replace_img, text)
    
    # Check choices formatting: if markdown list like - A. xx - B. xx, convert to \begin{choices}
    if r"\begin{choices}" not in text and re.search(r'^\s*[-*]?\s*[A-D][\.、\s]', text, re.MULTILINE):
        lines = text.split('\n')
        stem_lines = []
        choices_items = []
        for line in lines:
            m = re.match(r'^\s*[-*]?\s*([A-D])[\.、\s]+(.*)', line)
            if m:
                choices_items.append(f"    \\item {m.group(2).strip()}")
            else:
                if not choices_items:
                    stem_lines.append(line)
                else:
                    stem_lines.append(line)
        if choices_items:
            choices_block = "  \\begin{choices}\n" + "\n".join(choices_items) + "\n  \\end{choices}"
            text = "\n".join(stem_lines).strip() + "\n" + choices_block

    # For choice questions (single_choice, multi_choice), ensure \paren is present before \begin{choices} if not already present
    if q_type in ["single_choice", "multi_choice"]:
        if r"\begin{choices}" in text:
            parts = text.split(r"\begin{choices}", 1)
            stem = clean_choice_stem_parentheses(parts[0])
            rest = r"\begin{choices}" + parts[1]
            stem += r" \paren"
            text = stem + "\n  " + rest
        else:
            stem = clean_choice_stem_parentheses(text)
            text = stem + r" \paren"

    if is_answer:
        # 解答/解析步骤（is_answer=True）：在带小问/解答步骤前自动补全空行
        text = re.sub(r'([^\n])\n(?=[（(]?[0-9一二三四五六七八九十]+[）\.\)]|解[:：]|证明[:：]|因为|所以|故|又因为|由|综上|因此|得|【)', r'\1\n\n', text)
    else:
        # 题干文本处理：安全地按行首拆分与段落重组
        text = format_stem_paragraphs(text)

    return text

def build_latex_document(
    title: str,
    subtitle: str,
    paper_type: str,
    questions_data: list,
    include_answers: bool = False,
    show_secret: bool = True,
    show_notice: bool = True
) -> str:
    """
    Generate LaTeX source code based on exam-zh document class matching 试卷类模板.tex.
    questions_data: list of dicts with keys 'question' (Question dict) and 'score' (int).
    """
    paper_title = title.strip() or "2025 年普通高等学校招生全国统一考试(模拟卷)"
    sub_title = subtitle.strip()
    
    total_q_count = len(questions_data)
    total_score_sum = sum(q.get("score", 5) for q in questions_data)
    
    lines = []
    lines.append(r"\let\stop\empty")
    lines.append(r"\documentclass{exam-zh}")
    lines.append(r"\usepackage{siunitx}")
    lines.append(r"\usepackage{tkz-euclide}")
    lines.append(r"\usepackage{diagbox}")
    lines.append(r"\usepackage{lastpage}")
    lines.append(r"\usepackage{caption}")
    lines.append(r"\usepackage{wrapfig}")
    lines.append(r"\usepackage{graphicx}")
    lines.append(r"\usepackage{amsmath}")
    lines.append(r"\usepackage{adjustbox}")
    lines.append(r"\examsetup{")
    lines.append(r"  page/size=a4paper,")
    lines.append(r"  paren/show-paren=true,")
    if include_answers:
        lines.append(r"  paren/show-answer=true,")
        lines.append(r"  fillin/show-answer=true,")
        lines.append(r"  solution/show-solution=show-stay,")
    else:
        lines.append(r"  paren/show-answer=false,")
        lines.append(r"  fillin/show-answer=false,")
    lines.append(r"  font      = times,")
    lines.append(r"  math-font = xits,")
    lines.append(r"  fillin/no-answer-type=none")
    lines.append(r"}")
    lines.append(r"\newcommand{\customcurve}[1][1]{%")
    lines.append(r"  \tikz[baseline,yshift=3,scale=0.05,thick,line width=0.8pt,")
    lines.append(r"        line cap=round, domain=-1:2.832, samples=300]{")
    lines.append(r"    \draw plot (\x,{sqrt(16/(\x+2)^2 - (\x-2)^2)});")
    lines.append(r"    \draw plot (\x,{-sqrt(16/(\x+2)^2 - (\x-2)^2)});")
    lines.append(r"    }")
    lines.append(r"}")
    lines.append(r"\everymath{\displaystyle}")
    lines.append("")
    lines.append(f"\\title{{{paper_title}}}")
    lines.append(r"\subject{数学}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\raggedbottom")
    lines.append("")
    
    is_exam_style = (paper_type == "exam" or paper_type == "exam_19")
    if is_exam_style:
        if show_secret:
            lines.append(r"\secret")
        else:
            lines.append(r"% \secret")
        lines.append("")
    
    lines.append(r"\maketitle")
    
    if sub_title:
        tex_sub_title = re.sub(r' {2,}', lambda m: r'\ ' * len(m.group(0)), sub_title)
        lines.append(rf"\begin{{center}}\large\bfseries {tex_sub_title}\end{{center}}")
        
    if is_exam_style:
        lines.append(r"\begin{center}")
        lines.append(f"    本试卷共 \\pageref{{LastPage}} 页，{total_q_count} 题。全卷满分 {total_score_sum} 分。考试用时 120 分钟。")
        lines.append(r"\end{center}")
        lines.append("")
        if show_notice:
            lines.append(r"\begin{notice}")
            lines.append(r"  \item 答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。")
            lines.append(r"  \item 回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。")
            lines.append(r"  \item 考试结束后，将本试卷和答题卡一并交回。")
            lines.append(r"\end{notice}")
        else:
            lines.append(r"% \begin{notice}")
            lines.append(r"%   \item 答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。")
            lines.append(r"%   \item 回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。")
            lines.append(r"%   \item 考试结束后，将本试卷和答题卡一并交回。")
            lines.append(r"% \end{notice}")
        lines.append("")

    # Group questions by question_type
    grouped = {}
    for item in questions_data:
        q = item.get("question", {})
        q_type = q.get("question_type", "single_choice")
        if q_type not in grouped:
            grouped[q_type] = []
        grouped[q_type].append(item)
        
    for q_type in TYPE_ORDER:
        if q_type not in grouped or not grouped[q_type]:
            continue
        items = grouped[q_type]
        count = len(items)
        sec_score = sum(it.get("score", 5) for it in items)
        unit_score = items[0].get("score", 5) if count > 0 else 5
        
        # For exam_19, set fixed starting question number according to Gaokao layout:
        # 单选: 1
        # 多选: 9
        # 填空: 12
        # 解答: 15
        if paper_type == "exam_19":
            if q_type == "single_choice":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {1} \ExplSyntaxOff")
            elif q_type == "multi_choice":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {9} \ExplSyntaxOff")
            elif q_type == "fill_in_blank":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {12} \ExplSyntaxOff")
            elif q_type == "detailed_answer":
                lines.append(r"\ExplSyntaxOn \int_gset:Nn \g__examzh_question_index_int {15} \ExplSyntaxOff")

        if paper_type == "quiz":
            if q_type == "single_choice":
                section_header = "单选题"
            elif q_type == "multi_choice":
                section_header = "多选题"
            elif q_type == "fill_in_blank":
                section_header = "填空题"
            else:
                section_header = "解答题"
        else:
            if q_type == "single_choice":
                section_header = f"选择题：本题共 {count} 小题，每小题 {unit_score} 分，共 {sec_score} 分。\n  在每小题给出的四个选项中，只有一项是符合题目要求的。"
            elif q_type == "multi_choice":
                section_header = f"多选题：本题共 {count} 小题，每小题 {unit_score} 分，共 {sec_score} 分。\n  在每小题给出的四个选项中，有多项符合题目要求。\n  全部选对的得 {unit_score} 分，部分选对的得部分分，有选错的得 0 分。"
            elif q_type == "fill_in_blank":
                section_header = f"填空题：本题共 {count} 小题，每小题 {unit_score} 分，共 {sec_score} 分。"
            else:
                section_header = f"解答题：本题共 {count} 小题，共 {sec_score} 分。解答应写出文字说明、证明过程或演算步骤。"
            
        lines.append(f"\\section{{\n  {section_header}\n}}")
        lines.append("")
        
        for item in items:
            q = item.get("question", {})
            raw_content = q.get("content", "")
            q_score = item.get("score", 5)
            tikz = q.get("tikz_code", "").strip()

            fig_body = ""
            cleaned_raw = raw_content
            tikz_code = ""

            if tikz or r"\begin{tikzpicture}" in raw_content:
                tikz_code = tikz
                if not tikz_code and r"\begin{tikzpicture}" in raw_content:
                    m = re.search(r'(\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\})', raw_content)
                    if m:
                        tikz_code = m.group(1)
                        cleaned_raw = cleaned_raw.replace(tikz_code, '').strip()
                
                cleaned_raw = re.sub(r'!\[.*?\]\([^)]+\)', '', cleaned_raw).strip()
            fig_elements = []
            if tikz_code:
                if r"\begin{tikzpicture}" not in tikz_code:
                    tikz_code = f"\\begin{{tikzpicture}}\n{tikz_code}\n\\end{{tikzpicture}}"
                fig_elements.append(f"\\resizebox{{4.5cm}}{{!}}{{{tikz_code}}}")

            img_matches = re.findall(r'!\[.*?\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)', raw_content)
            if img_matches:
                for img_path in img_matches:
                    img_filename = os.path.basename(img_path)
                    # 避免在已经输出了矢量 tikz_code 时二次重叠渲染同名生成图 tikz_xxx.png
                    if tikz_code and img_filename.startswith("tikz_"):
                        continue
                    img_w = "3.8cm" if (len(img_matches) > 1 and not (tikz_code and img_filename.startswith("tikz_"))) or (tikz_code and not img_filename.startswith("tikz_")) else "5.0cm"
                    fig_elements.append(f"\\includegraphics[width={img_w}]{{{img_filename}}}")
                cleaned_raw = re.sub(r'!\[.*?\]\([^)]+\)', '', cleaned_raw).strip()

            if fig_elements:
                fig_body = "\n\\vspace{2pt}\n".join(fig_elements)

            cleaned_content = clean_content_for_latex(cleaned_raw, q_type=q_type)
            env_name = "problem" if q_type == "detailed_answer" else "question"
            points_arg = f"[points = {q_score}]" if q_type == "detailed_answer" else ""

            default_fig_align = "bottom_right" if paper_type == "quiz" else "right"
            fig_align = q.get("figure_align")
            if not fig_align or (paper_type == "quiz" and fig_align == "right" and not q.get("custom_figure_align")):
                fig_align = default_fig_align
            # 多张插图且原设定为右侧时，默认自动优化为下方居中 (center)
            if len(fig_elements) > 1 and fig_align == "right":
                fig_align = "center"

            lines.append(f"\\begin{{{env_name}}}{points_arg}")
            if fig_body:
                choices_part = ""
                if r"\begin{choices}" in cleaned_content:
                    parts = cleaned_content.split(r"\begin{choices}", 1)
                    stem_text = parts[0].strip()
                    choices_part = r"\begin{choices}" + parts[1]
                else:
                    stem_text = cleaned_content

                sol_space = item.get("solution_space") or q.get("solution_space") or "0.0"
                try:
                    space_val = float(sol_space)
                except Exception:
                    space_val = 0.0
                is_sol_spaced = (q_type == "detailed_answer" and not include_answers and space_val > 0)

                if fig_align == "center":
                    lines.append(stem_text)
                    lines.append(r"\begin{center}")
                    lines.append(r"  \vspace*{-0.4em}")
                    lines.append(f"  {fig_body}")
                    lines.append(r"\end{center}")
                elif fig_align == "bottom_right":
                    lines.append(stem_text)
                    lines.append(r"\begin{flushright}")
                    lines.append(r"  \vspace*{-0.4em}")
                    lines.append(f"  {fig_body}")
                    lines.append(r"\end{flushright}")
                else:  # default "right"
                    lines.append(r"\noindent\begin{minipage}[t]{\dimexpr\linewidth-5.8cm\relax}")
                    lines.append(r"  \setlength{\parindent}{2em}")
                    lines.append(r"  \hangindent=0pt")
                    lines.append(r"  \hangafter=0")
                    lines.append(f"  {stem_text}")
                    lines.append(r"\end{minipage}%")
                    lines.append(r"\hfill")
                    lines.append(r"\begin{minipage}[t]{5.2cm}")
                    lines.append(r"  \vspace{-2.0em}")
                    lines.append(r"  \raggedleft")
                    lines.append(f"  \\adjustbox{{valign=t}}{{{fig_body}}}")
                    lines.append(r"\end{minipage}")

                if choices_part:
                    lines.append(choices_part)
            else:
                lines.append(cleaned_content)
            
            # Inject solution space for detailed_answer questions on papers
            if q_type == "detailed_answer" and not include_answers:
                sol_space = item.get("solution_space") or q.get("solution_space") or "0.0"
                try:
                    space_val = float(sol_space)
                except Exception:
                    space_val = 0.0
                if space_val > 0:
                    if fig_body and fig_align in ["bottom_right", "center"]:
                        net_space = max(space_val - 3.2, 0.5)
                        lines.append(f"\\vspace*{{{net_space:.1f}cm}}")
                    else:
                        lines.append(f"\\vspace*{{{space_val:.1f}cm}}")

            lines.append(f"\\end{{{env_name}}}")

            if include_answers:
                ans_text = q.get("answer_markdown", "").strip()
                if ans_text:
                    lines.append(r"\begin{solution}")
                    lines.append(clean_content_for_latex(ans_text))
                    lines.append(r"\end{solution}")
            lines.append("")

    lines.append(r"\end{document}")
    return "\n".join(lines)

def collect_referenced_images(questions_data: list, uploads_dir: str) -> list:
    """
    Find all referenced image file paths in static/uploads directory.
    Returns list of absolute image file paths.
    """
    image_paths = []
    for item in questions_data:
        q = item.get("question", {})
        imgs = q.get("image_paths", [])
        if isinstance(imgs, list):
            for img_rel in imgs:
                abs_p = os.path.join(uploads_dir, os.path.basename(img_rel))
                if os.path.exists(abs_p) and abs_p not in image_paths:
                    image_paths.append(abs_p)
        
        # Also parse content for Markdown image paths
        content = q.get("content", "") + " " + q.get("answer_markdown", "")
        found = re.findall(r'!\[.*?\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)', content)
        for fname in found:
            abs_p = os.path.join(uploads_dir, os.path.basename(fname))
            if os.path.exists(abs_p) and abs_p not in image_paths:
                image_paths.append(abs_p)

    return image_paths

def compile_tex_to_pdf(tex_content: str, image_paths: list = None) -> tuple:
    """
    Compiles LaTeX string into PDF using system xelatex.
    Features an in-memory LRU cache based on TeX content and image mtime hashes.
    Returns (pdf_bytes, log_output_or_error).
    """
    if image_paths is None:
        image_paths = []

    # 1. Compute MD5 Cache Key from TeX content & image modification times
    img_signatures = []
    for img_p in image_paths:
        if os.path.exists(img_p):
            img_signatures.append(f"{img_p}:{os.path.getmtime(img_p)}")
    
    cache_raw_str = f"{tex_content}||{'|'.join(img_signatures)}"
    cache_key = hashlib.md5(cache_raw_str.encode("utf-8")).hexdigest()

    # 2. Check LRU Cache
    with _PDF_CACHE_LOCK:
        if cache_key in _PDF_CACHE:
            result = _PDF_CACHE.pop(cache_key)
            _PDF_CACHE[cache_key] = result
            if result[0] is not None:
                print(f"[PDF_CACHE_HIT] 🚀 Reusing cached PDF! Key: {cache_key[:10]}... (Total Cached: {len(_PDF_CACHE)})", flush=True)
                return result

    # 3. Cache Miss: Perform xelatex compilation
    print(f"[PDF_CACHE_MISS] ⚡ Compiling TeX via xelatex... Key: {cache_key[:10]}...", flush=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, "paper.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)
            
        # Copy images into temp_dir
        for img_p in image_paths:
            if os.path.exists(img_p):
                try:
                    shutil.copy(img_p, temp_dir)
                except Exception:
                    pass

        # Try compiling with xelatex (requires 2 passes to resolve \pageref{LastPage} and .aux references)
        try:
            cmd = ["xelatex", "-interaction=nonstopmode", "paper.tex"]
            # Pass 1: Generate .aux file and initial layout
            subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, timeout=30)
            # Pass 2: Resolve \pageref{LastPage} and cross references from .aux
            proc = subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True, timeout=30)
            
            pdf_path = os.path.join(temp_dir, "paper.pdf")
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as pf:
                    pdf_bytes = pf.read()
                
                # Save to LRU Cache on success
                with _PDF_CACHE_LOCK:
                    if cache_key in _PDF_CACHE:
                        _PDF_CACHE.pop(cache_key)
                    _PDF_CACHE[cache_key] = (pdf_bytes, proc.stdout)
                    while len(_PDF_CACHE) > _MAX_PDF_CACHE_SIZE:
                        _PDF_CACHE.popitem(last=False)

                return (pdf_bytes, proc.stdout)
            else:
                log_path = os.path.join(temp_dir, "paper.log")
                log_txt = proc.stdout + "\n" + proc.stderr
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                        log_txt += "\n" + lf.read()
                # Preserve both the first error and the final summary; either can
                # contain the actionable diagnostic depending on the TeX engine.
                first_error = log_txt.find("\n!")
                if len(log_txt) <= 8000:
                    diagnostic = log_txt
                elif first_error >= 0:
                    diagnostic = f"{log_txt[max(0, first_error - 1000):first_error + 3000]}\n...\n{log_txt[-4000:]}"
                else:
                    diagnostic = f"{log_txt[:2000]}\n...\n{log_txt[-6000:]}"
                return (None, f"编译失败，无法生成 PDF:\n{diagnostic}")
        except FileNotFoundError:
            return (None, "系统未检测到 xelatex 编译器，请确保已安装 TeX Live / MiKTeX / MacTeX 并加入 PATH。")
        except subprocess.TimeoutExpired:
            return (None, "LaTeX 编译超时（超过 30 秒）。")
        except Exception as e:
            return (None, f"编译过程异常: {str(e)}")

def extract_figures_for_answer_sheet(content: str) -> str:
    """Extract TikZ code or images from question content for answer sheet embedding."""
    if not content:
        return ""
    figs = []
    # 1. TikZ blocks
    tikz_blocks = re.findall(r'(\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\})', content)
    for t in tikz_blocks:
        figs.append(t)
    # 2. Markdown image ![...](path)
    img_matches = re.findall(r'!\[.*?\]\((?:/static/uploads/|static/uploads/|/uploads/|uploads/)?([^)]+)\)', content)
    for img_name in img_matches:
        base_n = os.path.basename(img_name)
        if tikz_blocks and base_n.startswith("tikz_"):
            continue
        img_tag = f"\\includegraphics[max width=3.4cm]{{{base_n}}}"
        if img_tag not in figs:
            figs.append(img_tag)
    # 3. Direct \includegraphics
    direct_imgs = re.findall(r'(\\includegraphics(?:\[.*?\])?\{.*?\})', content)
    for d in direct_imgs:
        if d not in figs:
            figs.append(d)
    return "\n".join(figs)

def clear_pdf_cache():
    """Clear all entries in PDF compilation LRU cache."""
    with _PDF_CACHE_LOCK:
        _PDF_CACHE.clear()
        print("[PDF_CACHE] 🧹 PDF LRU Memory Cache Cleared Successfully!", flush=True)

# 清空历史旧 PDF 缓存，确保最新段落与排版算法秒级生效
clear_pdf_cache()

def build_answer_sheet_latex(title: str, subtitle: str, questions_data: list) -> str:
    """
    Generate Answer Sheet (答题卡.tex) LaTeX code based on templates/答题卡.tex or built-in default.
    Dynamic updates: title, question scores, and embedded TikZ/image nodes for Q15-Q19.
    """
    template_path = str(TEMPLATES_DIR / "答题卡.tex")
    content = ""
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            pass

    if not content:
        content = r"""
\documentclass[UTF8, fontset=fandol, 11pt, oneside]{ctexart}
\usepackage{amsmath, amsthm, amssymb, graphicx}
\usepackage[bookmarks=true, colorlinks, citecolor=blue, linkcolor=black]{hyperref}
\usepackage[a3paper, landscape,left=0.7cm, right=0.7cm, top=0.6cm, bottom=0.6cm]{geometry}
\usepackage{diagbox, tikz, fancyhdr, makecell, caption, float, eso-pic}
\linespread{1.625}
\setcounter{page}{1}
\pagestyle{fancy}
\fancyhf{}
\definecolor{mycolor}{RGB}{255,50,165}
\AddToShipoutPictureFG{%
    \begin{tikzpicture}[remember picture, overlay]
        \fill[black] ([xshift=0.7cm, yshift=0.6cm] current page.south west) rectangle ++(10mm, 6mm);
        \fill[black] ([xshift=13.9cm, yshift=0.6cm] current page.south west) rectangle ++(10mm, 6mm);
        \fill[black] ([xshift=27.1cm, yshift=0.6cm] current page.south west) rectangle ++(10mm, 6mm);
        \fill[black] ([xshift=-0.7cm, yshift=0.6cm] current page.south east) rectangle ++(-10mm, 6mm);
        \fill[black] ([xshift=-0.7cm, yshift=-0.6cm] current page.north east) rectangle ++(-10mm, -6mm);
        \fill[black] ([xshift=0.7cm, yshift=-0.6cm] current page.north west) rectangle ++(10mm, -6mm);
        \fill[black] ([xshift=13.9cm, yshift=-0.6cm] current page.north west) rectangle ++(10mm, -6mm);
        \fill[black] ([xshift=27.1cm, yshift=-0.6cm] current page.north west) rectangle ++(10mm, -6mm);
        \node at (current page.south) [anchor=south, yshift=0.6cm] {\color{mycolor}\textbf{数学答题卡第\thepage 面（共2面）}};
    \end{tikzpicture}%
}
\begin{document}
\centering
\begin{tikzpicture}
    \useasboundingbox (0,0) rectangle (39.2,28.25);
    \node[font=\fontsize{16pt}{16pt}\selectfont] at (6.45,27.3) {\color{mycolor}\textbf{2026年普通高等学校招生全国统一考试}};
    \node[font=\fontsize{22pt}{22pt}\selectfont] at (6.45,26.3){\textbf{数学答题卡}};
    \node at (6.45,25) {考场号：\textcolor{mycolor}{\underline{\hspace{1.3cm}}} \hspace{0.6em}座位号：\textcolor{mycolor}{\underline{\hspace{1.3cm}}}\hspace{0.6em}姓名：\textcolor{mycolor}{\underline{\hspace{2.2cm}}} \hspace{0.6em}班级：\textcolor{mycolor}{\underline{\hspace{1.8cm}}}};
    
    \def\rows{10}\def\cols{10}\def\cellw{0.8}\def\cellh{0.5}\def\beginx{4.9}\def\beginy0{24.2}\def\fontHeight{0.6}\def\smallHeight{0.2}\def\selectw{0.5}\def\selecth{0.25}
    \foreach \col [evaluate=\col as \x using \beginx+\col*\cellw] in {0,...,\cols} { \draw[mycolor] (\x, \beginy0-\fontHeight) -- (\x,\beginy0-\fontHeight-\cellw-\cellh*\rows-2*\smallHeight); }
    \draw[mycolor] (\beginx,\beginy0-\fontHeight) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight);
    \draw[mycolor] (\beginx,\beginy0) -- (\beginx+\cellw*\cols,\beginy0);
    \draw[mycolor] (\beginx,\beginy0) -- (\beginx,\beginy0-\fontHeight);
    \draw[mycolor] (\beginx+\cellw*\cols,\beginy0) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight);
    \draw[mycolor] (\beginx,\beginy0-\fontHeight-\cellw) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight-\cellw);
    \draw[mycolor] (\beginx,\beginy0-\fontHeight-\cellw-\smallHeight*2-\cellh*\rows) -- (\beginx+\cellw*\cols,\beginy0-\fontHeight-\cellw-\smallHeight*2-\cellh*\rows);
    \node at (\beginx+0.5*\cols*\cellw,\beginy0-0.5*\fontHeight) {\small\textbf{考\hspace{1em}生\hspace{1em}号}};
    \foreach \row in {1,...,\rows} {
        \foreach \col in {1,...,\cols} {
            \node at (\beginx+\col*\cellw-0.5*\cellw,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh) {\footnotesize\textcolor{mycolor}{\the\numexpr \row - 1 \relax}};
            \draw[mycolor] (\beginx+\col*\cellw-0.5*\cellw-0.5*\selectw+0.25*\selectw,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh+0.5*\selecth) -- ++(-0.25*\selectw,0) -- ++(0,-\selecth) -- ++(0.25*\selectw,0);
            \draw[mycolor] (\beginx+\col*\cellw-0.5*\cellw-0.5*\selectw+0.75*\selectw,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh+0.5*\selecth) -- ++(0.25*\selectw,0) -- ++(0,-\selecth) -- ++(-0.25*\selectw,0);
        }
    }
    \foreach \row in {1,...,\rows} { \fill[black] (-0.7,\beginy0-\fontHeight-\cellw-\smallHeight-\row*\cellh+0.5*\cellh+0.5*\selecth) rectangle ++(\selectw,-\selecth); }
    \foreach \col in{1,2,...,15} { \fill[black] (\beginx+\col*\cellw-0.5*\cellw-0.5*\selectw-4.5,28.1) rectangle ++(\selectw,-\selecth); }

    \def\startx{0}\def\starty{24.2}\def\squarew{4.7}\def\squareh{6.8}\def\fontmarginx{0.1}\def\fontmarginy{0.1}
    \draw[mycolor] (\startx,\starty) rectangle ++(\squarew,-\squareh);
    \node[text width=4.3cm,align=left,anchor=north west] at (\startx+\fontmarginx,\starty-\fontmarginy)
        {\fontsize{8pt}{8pt}\selectfont\textbf{注意事项}：\\1．答题前，考生务必用黑色字迹的钢笔或签字笔将考场号、座位号、姓名和考生号填写在答题卡上，并用2B铅笔将考生号对应的数字涂黑。\\2．选择题的选出每小题答案后，用2B铅笔把答题卡上对应题目的答案标号涂黑。如需改动，用橡皮擦擦干净后，再选其它答案标号涂黑。非选择题的答案不能超出指定答题区域。\\3．答题卡保持卡面整洁，不要折叠和弄破。\\};

    \def\bigx{0}\def\bigy{16.9}\def\bigw{12.9}\def\bigh{16.3}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\chosex{0.2}\def\chosey{16.2}\def\chosew{12.5}\def\choseh{2.7}
    \draw[black] (\chosex,\chosey) rectangle ++(\chosew,-\choseh);
    \node[anchor=north west, align=left, font=\fontsize{10pt}{9pt}\selectfont] at (\chosex+0.1, \chosey+0.6) {\textbf{一、选择题}\\};
    \def\chosenumx{0.7}\def\chosenumy{15.67}\def\chosedistancey{0.55}\def\numenglishd{0.7}\def\chosedistancex{0.75}\def\distance{1.1}\def\quantity{11}
    \pgfmathsetmacro{\d}{\numenglishd+3*\chosedistancex+\distance}
    \foreach \row in {0,...,\numexpr\quantity-1\relax} {
        \pgfmathtruncatemacro{\nx}{floor(\row/4)}
        \node at (\chosenumx+\nx*\d,\chosenumy-\row*\chosedistancey+\nx*4*\chosedistancey) {\the\numexpr \row + 1 \relax};
        \foreach \col in {0,1,2,3} {
            \pgfmathsetmacro{\x}{{"A","B","C","D"}[\col]}
            \node at (\chosenumx+\numenglishd+\chosedistancex*\col+\nx*\d,\chosenumy-\row*\chosedistancey+\nx*4*\chosedistancey) {\footnotesize\textcolor{mycolor}{\x}};
            \draw[mycolor] (\chosenumx+\numenglishd-0.25*\selectw+\chosedistancex*\col+\nx*\d,\chosenumy-\row*\chosedistancey+0.5*\selecth+\nx*4*\chosedistancey) -- ++(-0.25*\selectw,0) -- ++(0,-\selecth) -- ++(0.25*\selectw,0);
            \draw[mycolor] (\chosenumx+\numenglishd+0.25*\selectw+\chosedistancex*\col+\nx*\d,\chosenumy-\row*\chosedistancey+0.5*\selecth+\nx*4*\chosedistancey) -- ++(0.25*\selectw,0) -- ++(0,-\selecth) -- ++(-0.25*\selectw,0);
        }
    }
    \foreach \row in{0,1,2,3} { \fill[black] (-0.7,\chosenumy-\row*\chosedistancey+0.5*\selecth) rectangle ++(\selectw,-\selecth); }

    \def\blankx{0.2}\def\blanky{12.8}\def\blankw{12.5}\def\blankh{1.4}
    \def\blanknumx{0.7}\def\blanknumy{12}
    \pgfmathsetmacro{\distancew}{\numenglishd + 3*\chosedistancex + \distance}
    \draw[black] (\blankx,\blanky) rectangle ++(\blankw,-\blankh);
    \node[anchor=north west, align=left, font=\fontsize{10pt}{9pt}\selectfont] at (\blankx+0.1, \blanky+0.6) {\textbf{二、填空题}\\};
    \foreach \row in {0,1,2} {
        \node at (\blanknumx+\row*\distancew+0.1,\blanknumy) {\the\numexpr \row + 12 \relax.};
        \draw[mycolor] (\blanknumx+\row*\distancew+0.4,\blanknumy-0.2) -- ++(\distancew-1,0);
    }
    
    \def\ebx{0.2}\def\eby{10.7}\def\ebw{12.5}\def\ebh{9.5}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    \node[anchor=north west, align=left, font=\fontsize{10pt}{9pt}\selectfont] at (\ebx+0.1, \eby+0.6) {\textbf{三、解答题}\\};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{1.5}\def\exy{10.2}
    \node at (\exx,\exy) {15.（13分）};

    \def\bigx{13.2}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{13.4}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    \draw (\ebx,\eby-0.25*\ebh) -- (\ebx+\ebw,\eby-0.25*\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{14.7}\def\exy{26.3}
    \node at (\exx,\exy-0.25*\ebh) {16.（15分）};
    \node at (\exx,\exy) {\textcolor{mycolor}{\textbf{（续15题）}}};

    \def\bigx{26.4}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{26.6}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{27.9}\def\exy{26.3}
    \node at (\exx,\exy) {17.（15分）};
\end{tikzpicture}
\newpage
\begin{tikzpicture}
    \useasboundingbox (0,0) rectangle (39.2,28.25);
    \def\bigx{0}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{0.2}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};

    \def\exx{1.5}\def\exy{26.3}
    \node at (\exx,\exy) {18.（17分）};

    \def\bigx{13.2}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{13.4}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    \draw (\ebx,\eby-0.5*\ebh) -- (\ebx+\ebw,\eby-0.5*\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{14.7}\def\exy{26.3}
    \node at (\exx,\exy-0.5*\ebh) {19.（17分）};
    \node at (\exx,\exy) {\textcolor{mycolor}{\textbf{（续18题）}}};

    \def\bigx{26.4}\def\bigy{27.4}\def\bigw{12.9}\def\bigh{26.8}
    \draw[rounded corners=10pt,mycolor] (\bigx,\bigy) rectangle ++(\bigw,-\bigh);

    \def\ebx{26.6}\def\eby{26.8}\def\ebw{12.5}\def\ebh{25.6}
    \draw[black] (\ebx,\eby) rectangle ++(\ebw,-\ebh);
    
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-\bigh+0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    \node[text=mycolor] at (\bigx+\bigw*0.5,\bigy-0.3) {\small 请在各题目的答题区域内作答，超出黑色矩形边框限定区域的答案无效};
    
    \def\exx{27.9}\def\exy{26.3}
    \node at (\exx,\exy) {\textcolor{mycolor}{\textbf{（续19题）}}};
\end{tikzpicture}
\end{document}
"""

    lines = content.splitlines()
    cleaned_lines = []
    for line in lines:
        sline = line.strip()
        if sline.startswith("作者：") or sline.startswith("链接：") or sline.startswith("来源：") or sline.startswith("著作权归作者所有"):
            cleaned_lines.append("% " + line)
        else:
            cleaned_lines.append(line)
    tex_str = "\n".join(cleaned_lines)

    paper_title = title.strip() or "2026年普通高等学校招生全国统一考试"
    tex_str = re.sub(r'202\d年普通高等学校招生全国统一考试', paper_title, tex_str)

    detailed_questions = []
    for item in questions_data:
        q = item.get("question", {})
        if q.get("question_type") == "detailed_answer":
            detailed_questions.append(item)

    # Map for Q15..Q19 (full node line, template line, figure top-right anchor coordinates locked 0.7cm inside right border)
    coords_map = [
        ("15", r"\node at (\exx,\exy) {15.（13分）};", r"\node at (\exx,\exy) {{15.（{score}分）}};", "12.0, 10.2"),
        ("16", r"\node at (\exx,\exy-0.25*\ebh) {16.（15分）};", r"\node at (\exx,\exy-0.25*\ebh) {{16.（{score}分）}};", "25.2, 20.0"),
        ("17", r"\node at (\exx,\exy) {17.（15分）};", r"\node at (\exx,\exy) {{17.（{score}分）}};", "38.4, 26.3"),
        ("18", r"\node at (\exx,\exy) {18.（17分）};", r"\node at (\exx,\exy) {{18.（{score}分）}};", "12.0, 26.3"),
        ("19", r"\node at (\exx,\exy-0.5*\ebh) {19.（17分）};", r"\node at (\exx,\exy-0.5*\ebh) {{19.（{score}分）}};", "25.2, 13.5"),
    ]

    for idx in range(min(5, len(detailed_questions))):
        item = detailed_questions[idx]
        q = item.get("question", {})
        score = item.get("score", 15)
        q_num, default_line, new_line_tmpl, fig_coords = coords_map[idx]

        new_line = new_line_tmpl.format(score=score)

        # Check for figures
        q_content = q.get("content", "")
        tikz_code = q.get("tikz_code", "").strip()
        full_content = q_content + ("\n" + tikz_code if tikz_code else "")
        fig_code = extract_figures_for_answer_sheet(full_content)
        if fig_code:
            fig_node = f"\\node[anchor=north east, inner sep=0pt, outer sep=0pt] at ({fig_coords}) {{\\resizebox{{4.2cm}}{{!}}{{{fig_code}}}}};"
            replacement = f"{new_line}\n    {fig_node}"
        else:
            replacement = new_line

        tex_str = tex_str.replace(default_line, replacement)

    return tex_str

def create_tex_zip_package(title: str, tex_content: str, ans_tex_content: str, image_paths: list, answer_sheet_tex: str = None) -> bytes:
    """
    Creates an in-memory ZIP package containing paper.tex, answer.tex, answer_sheet.tex (if present), and referenced images.
    """
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("试卷正文.tex", tex_content.encode("utf-8"))
        if ans_tex_content:
            zf.writestr("参考答案与解析.tex", ans_tex_content.encode("utf-8"))
        if answer_sheet_tex:
            zf.writestr("答题卡.tex", answer_sheet_tex.encode("utf-8"))
            
        for img_p in image_paths:
            if os.path.exists(img_p):
                fname = os.path.basename(img_p)
                zf.write(img_p, arcname=f"images/{fname}")

    return zip_buffer.getvalue()

def create_full_bundle_zip_package(
    title: str,
    tex_content: str,
    ans_tex_content: str,
    image_paths: list,
    answer_sheet_tex: str = None,
    main_pdf_bytes: bytes = None,
    ans_pdf_bytes: bytes = None,
    answer_sheet_pdf_bytes: bytes = None
) -> bytes:
    """
    Creates an in-memory ZIP package containing LaTeX TeX files, compiled PDF files, and referenced images.
    """
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. TeX files
        zf.writestr("试卷正文.tex", tex_content.encode("utf-8"))
        if ans_tex_content:
            zf.writestr("参考答案与解析.tex", ans_tex_content.encode("utf-8"))
        if answer_sheet_tex:
            zf.writestr("答题卡.tex", answer_sheet_tex.encode("utf-8"))
            
        # 2. PDF files
        if main_pdf_bytes:
            zf.writestr("试卷正文.pdf", main_pdf_bytes)
        if ans_pdf_bytes:
            zf.writestr("参考答案与解析.pdf", ans_pdf_bytes)
        if answer_sheet_pdf_bytes:
            zf.writestr("答题卡.pdf", answer_sheet_pdf_bytes)
            
        # 3. Referenced images
        for img_p in image_paths:
            if os.path.exists(img_p):
                fname = os.path.basename(img_p)
                zf.write(img_p, arcname=f"images/{fname}")

    return zip_buffer.getvalue()
