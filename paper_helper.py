import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from sqlalchemy.orm import Session
from database import Question, Paper, PaperQuestion, QuestionCurriculum

# Constants for question type labels
TYPE_LABELS = {
    "single_choice": "单项选择题",
    "multi_choice": "多项选择题",
    "fill_in_blank": "填空题",
    "detailed_answer": "解答题"
}

TYPE_ORDER = ["single_choice", "multi_choice", "fill_in_blank", "detailed_answer"]

def clean_content_for_latex(content: str, q_type: str = "") -> str:
    """
    Clean markdown/LaTeX question content for exam-zh LaTeX document export based on 试卷类模板.tex.
    Preserves math delimiters $...$ and $$...$$, tikz code, and handles choices & \paren environments.
    """
    if not content:
        return ""
    
    text = content.strip()
    
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
            stem = parts[0].strip()
            rest = r"\begin{choices}" + parts[1]
            if r"\paren" not in stem and r"\parentheses" not in stem:
                stem += r" \paren"
            text = stem + "\n  " + rest
        else:
            if r"\paren" not in text and r"\parentheses" not in text:
                text += r" \paren"

    return text

def build_latex_document(title: str, subtitle: str, paper_type: str, questions_data: list, include_answers: bool = False) -> str:
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
    lines.append(r"\usepackage{graphicx}")
    lines.append(r"\usepackage{amsmath,amssymb}")
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
    lines.append("")
    
    if paper_type == "exam":
        lines.append(r"\secret")
        lines.append("")
    
    lines.append(r"\maketitle")
    
    if sub_title:
        lines.append(rf"\begin{{center}}\large\bfseries {sub_title}\end{{center}}")
        
    if paper_type == "exam":
        lines.append(r"\begin{center}")
        lines.append(f"    本试卷共 \\pageref{{LastPage}} 页，{total_q_count} 题。全卷满分 {total_score_sum} 分。考试用时 120 分钟。")
        lines.append(r"\end{center}")
        lines.append("")
        lines.append(r"\begin{notice}")
        lines.append(r"  \item 答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。")
        lines.append(r"  \item 回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。")
        lines.append(r"  \item 考试结束后，将本试卷和答题卡一并交回。")
        lines.append(r"\end{notice}")
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
            
            cleaned_content = clean_content_for_latex(raw_content, q_type=q_type)
            
            if q_type == "detailed_answer":
                lines.append(f"\\begin{{problem}}[points = {q_score}]")
                lines.append(cleaned_content)
                lines.append(r"\end{problem}")
            else:
                lines.append(r"\begin{question}")
                lines.append(cleaned_content)
                lines.append(r"\end{question}")
            
            # If TikZ code exists and not in content
            tikz = q.get("tikz_code", "").strip()
            if tikz and "\\begin{tikzpicture}" not in cleaned_content:
                lines.append(r"\begin{center}")
                lines.append(tikz)
                lines.append(r"\end{center}")
                
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

def compile_tex_to_pdf(tex_content: str, image_paths: list) -> tuple:
    """
    Compiles LaTeX string into PDF using system xelatex.
    Returns (pdf_bytes, log_output_or_error).
    """
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
                return (pdf_bytes, proc.stdout)
            else:
                log_path = os.path.join(temp_dir, "paper.log")
                log_txt = proc.stdout + "\n" + proc.stderr
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                        log_txt += "\n" + lf.read()
                return (None, f"编译失败，无法生成 PDF:\n{log_txt[:2000]}")
        except FileNotFoundError:
            return (None, "系统未检测到 xelatex 编译器，请确保已安装 TeX Live / MiKTeX / MacTeX 并加入 PATH。")
        except subprocess.TimeoutExpired:
            return (None, "LaTeX 编译超时（超过 30 秒）。")
        except Exception as e:
            return (None, f"编译过程异常: {str(e)}")

def create_tex_zip_package(title: str, tex_content: str, ans_tex_content: str, image_paths: list) -> bytes:
    """
    Creates a in-memory ZIP package containing paper.tex, answer.tex, and referenced images.
    """
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("试卷正文.tex", tex_content.encode("utf-8"))
        if ans_tex_content:
            zf.writestr("参考答案与解析.tex", ans_tex_content.encode("utf-8"))
            
        for img_p in image_paths:
            if os.path.exists(img_p):
                fname = os.path.basename(img_p)
                zf.write(img_p, arcname=f"images/{fname}")

    return zip_buffer.getvalue()
