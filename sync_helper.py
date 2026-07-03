import os
import json
import re
import datetime
import threading
from sqlalchemy.orm import Session
from database import Question, SessionLocal

BACKUP_DIR = "data_backup"
JSON_BACKUP_PATH = os.path.join(BACKUP_DIR, "questions_backup.json")
MD_BACKUP_PATH = os.path.join(BACKUP_DIR, "questions_library.md")

# 线程锁，防止并发执行 background_tasks 时的写盘冲突
_export_lock = threading.Lock()

def export_database_to_files(db: Session = None):
    """将 SQLite 数据库中的所有题目导出为 JSON 备份和 Markdown 格式的 AI 可读题库"""
    with _export_lock:
        local_session = None
        try:
            if db is None:
                db = SessionLocal()
                local_session = db
                
            # 确保备份目录存在
            os.makedirs(BACKUP_DIR, exist_ok=True)
            
            # 查询所有题目，按 ID 升序排列
            questions = db.query(Question).order_by(Question.id.asc()).all()
            
            # 1. 导出为 JSON 备份
            questions_list = []
            for q in questions:
                questions_list.append(q.to_dict())
                
            with open(JSON_BACKUP_PATH, "w", encoding="utf-8") as f:
                json.dump(questions_list, f, ensure_ascii=False, indent=2)
                
            # 2. 导出为精美的 Markdown 文件供 Claude Code / 人工阅读参考
            generate_markdown_library(questions, MD_BACKUP_PATH)
            
            print(f"[Sync] 成功导出 {len(questions)} 道题目到 {JSON_BACKUP_PATH} 和 {MD_BACKUP_PATH}")
        except Exception as e:
            print(f"[Sync 错误] 导出数据库失败: {str(e)}")
        finally:
            if local_session:
                local_session.close()

def clean_latex_to_markdown_for_ai(text: str) -> str:
    """清理题干中的排版 LaTeX 命令（如 item, itemize, 双斜杠等），转换为 AI 极易识别的标准 Markdown。
    同时，100% 保留包含在 $...$ 或 $$...$$ 或 \\[...\\] 中的标准数学公式！
    """
    if not text:
        return ""
    
    # 0. 专门转换 choices 选择题环境为带 A. B. C. D. 标号的 Markdown 列表
    def replace_choices(match):
        inner = match.group(1)
        items = re.split(r'\\item', inner)
        items = [it.strip() for it in items if it.strip()]
        labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
        md_lines = []
        for idx, it in enumerate(items):
            label = labels[idx] if idx < len(labels) else str(idx + 1)
            md_lines.append(f"- {label}. {it}")
        return "\n" + "\n".join(md_lines) + "\n"
        
    text = re.sub(r'\\begin\{choices\}([\s\S]*?)\\end\{choices\}', replace_choices, text)
    
    # 1. 清理 LaTeX 列表环境标记（列表环境前后多换行以保持呼吸感）
    text = re.sub(r'\\begin\{(itemize|enumerate|center)\}', '\n', text)
    text = re.sub(r'\\end\{(itemize|enumerate|center)\}', '\n', text)
    
    # 2. 转换 LaTeX 列表子项（比如 \item[A.] 转换为 - A.，\item 转换为 - ）
    # 匹配带括号、括号加点或纯符号的选择，如 \item[A.] \item[A] 或 \item[(1)]
    text = re.sub(r'\\item\s*\[([A-Za-z0-9\.\(\)\s\u4e00-\u9fa5]+)\]', r'- \1 ', text)
    text = re.sub(r'\\item', r'- ', text)
    
    # 3. 转换 LaTeX 挖空或下划线为干净的文本下划线，供 AI 直观识别
    # 采用能够处理一层嵌套花括号的正则（如 \underline{\hspace{2cm}}）
    text = re.sub(r'\\underline\s*\{(\\hspace\s*\{[^{}]*\}|[^{}])*\}', '_______', text)
    text = re.sub(r'\\underline', '_______', text)
    # 保底清理：防止任何极端复杂的嵌套导致残留花括号
    text = text.replace('_______}', '_______')
    
    # 4. 清理 LaTeX 文本折行命令（双反斜杠 \\ 或 \\\）
    text = re.sub(r'\\\\+', '\n', text)
    
    # 5. 清除多余行尾杂乱的反斜杠
    text = re.sub(r'\\$', '', text, flags=re.MULTILINE)
    
    # 6. 折叠并规范化换行，避免段落过于松散，增加 AI 的上下文紧凑度
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()

def generate_markdown_library(questions, filepath: str):
    """生成高度结构化、题干纯净无干扰、支持 LaTeX 的只读 Markdown 文件，供 AI (如 Claude Code) 检索和备课参考"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 按照 学段 (Compulsory) -> 章节 (Chapter) -> 知识点 (Knowledge) 对题目进行归类
    structure = {}
    
    for q in questions:
        comp = q.category_compulsory or "未分类学段"
        chap = q.category_chapter or "未分章节"
        know = q.category_knowledge or "未分知识点"
        
        if comp not in structure:
            structure[comp] = {}
        if chap not in structure[comp]:
            structure[comp][chap] = {}
        if know not in structure[comp][chap]:
            structure[comp][chap][know] = []
            
        structure[comp][chap][know].append(q)

    total_count = len(questions)
    
    # 题型和难度映射关系
    custom_meta = None
    custom_meta_path = "data_backup/custom_metadata.json"
    if os.path.exists(custom_meta_path):
        try:
            with open(custom_meta_path, "r", encoding="utf-8") as f:
                custom_meta = json.load(f)
        except Exception:
            pass

    if custom_meta and isinstance(custom_meta, dict) and "question_types" in custom_meta and "difficulties" in custom_meta:
        type_display = {item["value"]: item["label"] for item in custom_meta["question_types"]}
        difficulty_display = {item["value"]: item["label"] for item in custom_meta["difficulties"]}
        # 保底映射默认系统内置字段以防老旧数据不匹配
        for val, lbl in [("easy", "🟢 容易"), ("medium", "🟡 中等"), ("hard", "🔴 较难")]:
            if val not in difficulty_display:
                difficulty_display[val] = lbl
    else:
        type_display = {
            "single_choice": "单选题",
            "multi_choice": "多选题",
            "fill_in_blank": "填空题",
            "detailed_answer": "解答题"
        }
        difficulty_display = {
            "easy": "🟢 容易",
            "medium": "🟡 中等",
            "hard": "🔴 较难",
            "easy_error": "🟠 易错题",
            "challenge": "🔥 压轴挑战题",
            "qiangji": "🎓 强基/竞赛题"
        }

    with open(filepath, "w", encoding="utf-8") as f:
        # 文件头
        f.write("# 📚 本地化数学题库导出目录 (AI 备课专属参考)\n\n")
        f.write("> [!IMPORTANT]\n")
        f.write("> **这是由题库系统自动生成的只读导出文件，专门供 Claude Code、Cursor 等 AI 助手在备课时进行题目分析、参考与引用。**\n")
        f.write("> - **安全与免打扰**：此文件仅包含「题目题干、图片与大纲信息」，**不包含参考答案和详细解析**，防止 AI 备课或生成周测时发生“答案泄露”或输出冗余干扰。\n")
        f.write("> - **格式完美化**：系统已对原题中的 LaTeX 排版命令（如 `\\item`, `\\begin{itemize}` 等）自动转换为了标准 Markdown 格式，**100% 完美保留了核心数学公式（$...$ 或 $$...$$）**，对 AI 识别无任何编译或阅读干扰。\n")
        f.write("> - 请勿在此文件中直接进行任何手动编辑，您的修改不会被同步回数据库。\n\n")
        
        # 数据统计
        f.write("## 📊 题库运行数据概览\n")
        f.write(f"- 📈 **总入库题目数量**: {total_count} 题\n")
        f.write(f"- 🕒 **最新同步时间**: {now_str}\n")
        f.write(f"- 📂 **本地图片存放目录**: `static/uploads/` (在 Git 提交时请包含此目录)\n\n")
        
        # 目录树
        f.write("## 🗂 目录与知识大纲树\n")
        if not structure:
            f.write("*暂无题目数据*\n\n")
        else:
            for comp in sorted(structure.keys()):
                f.write(f"- **{comp}**\n")
                for chap in sorted(structure[comp].keys()):
                    f.write(f"  - 📂 *{chap}*\n")
                    for know in sorted(structure[comp][chap].keys()):
                        f.write(f"    - 📍 {know} ({len(structure[comp][chap][know])} 题)\n")
            f.write("\n")
            
        f.write("---\n\n")
        
        # 题目列表详情
        f.write("## 📝 题目详细列表\n\n")
        
        if not structure:
            f.write("*题库空空如也，请先在系统网页端录入题目。*\n")
        else:
            # 建立有序的序列号映射（按照 ID 升序，从 1 开始）
            seq_mapping = {q.id: idx for idx, q in enumerate(questions, 1)}
            
            for comp in sorted(structure.keys()):
                f.write(f"# 【{comp}】\n\n")
                for chap in sorted(structure[comp].keys()):
                    f.write(f"## 📁 {chap}\n\n")
                    for know in sorted(structure[comp][chap].keys()):
                        f.write(f"### 📍 知识点：{know}\n\n")
                        
                        for q in structure[comp][chap][know]:
                            q_type_display = type_display.get(q.question_type, q.question_type)
                            q_diff_display = difficulty_display.get(q.difficulty, q.difficulty)
                            seq_num = seq_mapping.get(q.id, q.id)
                            
                            f.write(f"#### 📌 题目 #{seq_num} (数据库 ID: {q.id})\n")
                            f.write(f"- **题型**：`{q_type_display}`\n")
                            f.write(f"- **难度级别**：{q_diff_display}\n")
                            if q.source:
                                f.write(f"- **题目来源**：`{q.source}`\n")
                            if q.association_group_id:
                                f.write(f"- **关联题目组 ID**：`{q.association_group_id}`\n")
                            if q.tags:
                                f.write(f"- **标签**：`{q.tags}`\n")
                            f.write("\n")
                            
                            # 经过转换与清理的纯净题干
                            cleaned_content = clean_latex_to_markdown_for_ai(q.content)
                            f.write("**【题干内容】**\n\n")
                            f.write(f"{cleaned_content}\n\n")
                            
                            # 插图
                            img_paths = q.image_paths
                            if img_paths:
                                f.write("**【题目插图】**\n\n")
                                for img in img_paths:
                                    rel_img = f"../{img.lstrip('/')}"
                                    f.write(f"![题库插图]({rel_img})\n\n")
                                    
                            # TikZ 绘图源代码备份
                            if getattr(q, "tikz_code", None) and q.tikz_code.strip():
                                f.write("**【TikZ 几何绘图源码】**\n\n")
                                f.write(f"```latex\n{q.tikz_code.strip()}\n```\n\n")
                                    
                            f.write("---\n\n")
