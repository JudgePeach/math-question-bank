import os
import json
import re
import datetime
import threading
import tempfile
import sqlite3
from pathlib import Path
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import inspect
from mathbank.database import Question, QuestionCurriculum, SessionLocal
from mathbank.paths import DATA_BACKUP_DIR
from mathbank.sub_knowledge import resolve_active_version
from mathbank.content_sources import (
    CONTENT_SOURCES_JSON_NAME, CONTENT_SOURCES_NOTICE_NAME,
    content_source_export_files, content_sources_notice,
)
from mathbank.asset_security import fsync_writable_file, replace_file_atomic
from mathbank.storage_access import check_directory_access

BACKUP_DIR = str(DATA_BACKUP_DIR)
JSON_BACKUP_PATH = os.path.join(BACKUP_DIR, "questions_backup.json")
MD_BACKUP_PATH = os.path.join(BACKUP_DIR, "questions_library.md")

# 线程锁，防止并发执行 background_tasks 时的写盘冲突
_export_lock = threading.Lock()


def _temporary_output_path(final_path: str) -> str:
    directory = os.path.dirname(final_path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=".mathbank-export-", dir=directory)
    os.close(descriptor)
    return temp_path


def _fsync_file(path: str) -> None:
    fsync_writable_file(path)


def serialize_exchange_questions(db, questions, active_version):
    """Preserve mirrors, point definitions and confirmed/suggested evidence."""
    questions = list(questions)
    # Historical v11/v12 archives remain exportable before migration.  The
    # top-level array is unchanged; v13 includes stable reference definitions,
    # never the physical display sequence number or HTML link markup.
    references_by_question = {}
    export_connection = db.connection()
    inspector = inspect(export_connection)
    schema_version = int(export_connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)
    if schema_version >= 13 and all(inspector.has_table(name) for name in ("question_source_labels", "question_references")):
        from mathbank.question_references import serialize_question_reference_data
        references_by_question = serialize_question_reference_data(db, [question.id for question in questions])
    mappings_by_question = {}
    question_ids = list(dict.fromkeys(question.id for question in questions))
    for start in range(0, len(question_ids), 500):
        for mapping in db.query(QuestionCurriculum).options(
            selectinload(QuestionCurriculum.sub_knowledge_rows)
        ).filter(QuestionCurriculum.question_id.in_(question_ids[start:start + 500])).order_by(
            QuestionCurriculum.question_id, QuestionCurriculum.version_code
        ):
            mappings_by_question.setdefault(mapping.question_id, []).append({
                "version_code": mapping.version_code, "compulsory": mapping.compulsory,
                "chapter": mapping.chapter, "knowledge": mapping.knowledge,
                "sub_knowledge": mapping.sub_knowledge,
            })
    questions_list = []
    # These definitions all belong to the caller's one read snapshot. Reuse
    # their catalog trees within this export; relation evidence stays per row.
    point_definitions = {}
    for question in questions:
        # to_dict also reads the active mirror.  Reuse the bulk snapshot rather
        # than issuing one relationship query for every question.
        mappings = mappings_by_question.get(question.id, [])
        active = next((mapping for mapping in mappings if mapping["version_code"] == active_version), None)
        payload = question.to_dict(version_code=active_version, sub_knowledge=active["sub_knowledge"] if active else [])
        point_payloads = []
        for row in question.knowledge_points:
            if row.point_code not in point_definitions:
                point_definitions[row.point_code] = row.point.to_dict()
            point_payloads.append({**point_definitions[row.point_code], **row.to_dict(), "position": row.position})
        payload["knowledge_points"] = point_payloads
        payload["active_version_code"] = active_version
        payload["curriculum_mappings"] = mappings
        if active and any(payload["category_" + field] != active[field] for field in ("compulsory", "chapter", "knowledge")):
            raise RuntimeError("教材版本正在切换或主表镜像不一致，请稍后重试导出")
        payload["sub_knowledge"] = active["sub_knowledge"] if active else []
        if question.id in references_by_question:
            payload.update(references_by_question[question.id])
        questions_list.append(payload)
    return questions_list, mappings_by_question


def export_database_to_files(db: Session = None, *, quiet: bool = False):
    """Atomically export questions to portable JSON and AI-readable Markdown.

    These files are exports, not full disaster-recovery backups.  Use
    ``python3 -m scripts.backup`` for a verified full backup.
    """
    with _export_lock:
        local_session = None
        json_temp = None
        markdown_temp = None
        source_temps = {}
        try:
            # Check only outputs. A readonly source DB/images can still export.
            for directory in dict.fromkeys((Path(JSON_BACKUP_PATH).parent, Path(MD_BACKUP_PATH).parent)):
                check_directory_access(directory, label="题库同步导出目录（data_backup）")
            if db is None:
                db = SessionLocal()
                local_session = db
            # Python's legacy SQLite mode does not BEGIN for SELECT.  Establish
            # one real read snapshot before reading questions and relationships.
            connection = db.connection()
            raw_connection = connection.connection.dbapi_connection
            if isinstance(raw_connection, sqlite3.Connection) and not raw_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
                
            # 确保备份目录存在
            os.makedirs(BACKUP_DIR, exist_ok=True)
            
            # Freeze only IDs in this read snapshot.  Large relation evidence
            # must not remain in both all ORM rows and the complete JSON tree
            # while the independently formatted Markdown is being generated.
            question_ids = [row[0] for row in db.query(Question.id).order_by(Question.id.asc())]
            active_version = resolve_active_version()
            source_files = content_source_export_files()
            source_paths = {
                filename: os.path.join(os.path.dirname(JSON_BACKUP_PATH), filename)
                for filename in source_files
            }
            json_temp = _temporary_output_path(JSON_BACKUP_PATH)
            markdown_temp = _temporary_output_path(MD_BACKUP_PATH)

            # The Markdown helper needs display data, never full classification
            # evidence, answers or ORM relationships.  Keep its public interface
            # and grouping/order rules while retaining only these light views.
            from types import SimpleNamespace
            markdown_questions = []
            markdown_mappings = {}
            markdown_points = {}

            def write_batch(handle, batch_ids, first_record):
                questions = db.query(Question).filter(Question.id.in_(batch_ids)).order_by(Question.id.asc()).all()
                payloads, mappings = serialize_exchange_questions(db, questions, active_version)
                for payload in payloads:
                    # Prefixing each record's two-space pretty JSON reproduces
                    # json.dump(the_whole_array, ensure_ascii=False, indent=2)
                    # byte for byte without building that complete array.
                    handle.write("[\n  " if first_record else ",\n  ")
                    handle.write(json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", "\n  "))
                    first_record = False
                    question_id = payload["id"]
                    markdown_questions.append(SimpleNamespace(**{
                        field: payload[field] for field in (
                            "id", "content", "question_type", "category_compulsory",
                            "category_chapter", "category_knowledge", "difficulty", "source",
                            "association_group_id", "tags", "tikz_code", "content_tikz_assets",
                        )
                    }, display_image_paths=payload["image_paths"]))
                    markdown_mappings[question_id] = [
                        {"version_code": active_version, "sub_knowledge": mapping["sub_knowledge"]}
                        for mapping in mappings.get(question_id, [])
                        if mapping["version_code"] == active_version
                    ]
                    markdown_points[question_id] = [
                        {field: point[field] for field in ("name", "code", "role")}
                        for point in payload["knowledge_points"]
                    ]
                # Returning only a bool releases this batch's large JSON trees
                # and ORM rows; it never expunges caller-owned session objects.
                return first_record

            with open(json_temp, "w", encoding="utf-8") as f:
                first_record = True
                for start in range(0, len(question_ids), 128):
                    first_record = write_batch(f, question_ids[start:start + 128], first_record)
                f.write("[]" if first_record else "\n]")
                f.flush()
                os.fsync(f.fileno())

            # Generate both temporary files before replacing either good export.
            generate_markdown_library(
                markdown_questions, markdown_temp, curriculum_mappings=markdown_mappings,
                active_version_code=active_version,
                knowledge_points=markdown_points,
                source_notice=source_files[CONTENT_SOURCES_NOTICE_NAME].decode("utf-8"),
            )
            _fsync_file(markdown_temp)
            for filename, data in source_files.items():
                source_temp = _temporary_output_path(source_paths[filename])
                source_temps[filename] = source_temp
                with open(source_temp, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            replace_file_atomic(json_temp, JSON_BACKUP_PATH)
            json_temp = None
            replace_file_atomic(markdown_temp, MD_BACKUP_PATH)
            markdown_temp = None
            for filename, temp_path in list(source_temps.items()):
                replace_file_atomic(temp_path, source_paths[filename])
                del source_temps[filename]
            
            if not quiet:
                print(f"[Sync] 成功导出 {len(question_ids)} 道题目到 {JSON_BACKUP_PATH} 和 {MD_BACKUP_PATH}")
            return {
                "status": "success",
                "question_count": len(question_ids),
                "json_path": JSON_BACKUP_PATH,
                "markdown_path": MD_BACKUP_PATH,
                "content_sources_path": source_paths[CONTENT_SOURCES_JSON_NAME],
                "content_sources_notice_path": source_paths[CONTENT_SOURCES_NOTICE_NAME],
            }
        except Exception as e:
            raise RuntimeError(f"导出题目文件失败: {e}") from e
        finally:
            if json_temp:
                try:
                    os.unlink(json_temp)
                except FileNotFoundError:
                    pass
            if markdown_temp:
                try:
                    os.unlink(markdown_temp)
                except FileNotFoundError:
                    pass
            for temp_path in source_temps.values():
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
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

def generate_markdown_library(questions, filepath: str, *, curriculum_mappings=None, active_version_code=None, knowledge_points=None, source_notice=None):
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
    custom_meta_path = os.path.join(BACKUP_DIR, "custom_metadata.json")
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
        f.write("## 资料来源\n\n")
        f.write(content_sources_notice() if source_notice is None else source_notice)
        f.write("\n")
        
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
                            point_rows = (
                                knowledge_points.get(q.id, []) if knowledge_points is not None
                                else [row.to_dict() for row in q.knowledge_points]
                            )
                            for role, label in (("main", "主要考查知识点"), ("auxiliary", "辅助涉及知识点")):
                                matching_points = [
                                    point for point in point_rows
                                    if point.get("role") == role
                                ]
                                if matching_points:
                                    f.write(f"- **{label}**：" + "；".join(
                                        f"{point['name']} (`{point['code']}`)" for point in matching_points
                                    ) + "\n")
                            if curriculum_mappings is not None:
                                active = next((mapping for mapping in curriculum_mappings.get(q.id, []) if mapping["version_code"] == active_version_code), None)
                                fine_paths = active["sub_knowledge"] if active else []
                            else:
                                fine_paths = q.sub_knowledge_for_version(active_version_code or resolve_active_version())
                            if fine_paths:
                                f.write("- **细分知识点**：" + "；".join(" / ".join(path[field] for field in ("compulsory", "chapter", "knowledge")) for path in fine_paths) + "\n")
                            f.write("\n")
                            
                            # 经过转换与清理的纯净题干
                            cleaned_content = clean_latex_to_markdown_for_ai(q.content)
                            f.write("**【题干内容】**\n\n")
                            f.write(f"{cleaned_content}\n\n")
                            
                            # 插图
                            img_paths = q.display_image_paths
                            if img_paths:
                                f.write("**【题目插图】**\n\n")
                                for img in img_paths:
                                    rel_img = f"../{img.lstrip('/')}"
                                    f.write(f"![题库插图]({rel_img})\n\n")
                                    
                            # TikZ 绘图源代码备份（v5 多图，兼容旧单图字段）
                            tikz_codes = [
                                str(asset.get("tikz_code") or "").strip()
                                for asset in q.content_tikz_assets
                                if isinstance(asset, dict)
                                and str(asset.get("tikz_code") or "").strip()
                            ]
                            if not tikz_codes and getattr(q, "tikz_code", None):
                                legacy_tikz = q.tikz_code.strip()
                                if legacy_tikz:
                                    tikz_codes = [legacy_tikz]
                            for index, tikz_code in enumerate(tikz_codes, start=1):
                                title_suffix = f" {index}" if len(tikz_codes) > 1 else ""
                                f.write(f"**【TikZ 几何绘图源码{title_suffix}】**\n\n")
                                f.write(f"```latex\n{tikz_code}\n```\n\n")
                                    
                            f.write("---\n\n")
