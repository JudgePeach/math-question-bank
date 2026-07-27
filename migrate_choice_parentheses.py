import re
import os
import sys
from database import Base, Question, engine, SessionLocal
from sync_helper import export_database_to_files

def clean_choice_stem_parentheses(text: str) -> str:
    """清理选择题题干末尾供填答用的全角/半角空括号"""
    if not text:
        return ""
    pattern = r'(?:[\s\xa0]*[\(（]\s*(?:\\quad|\\qquad|\\hspace\{.*?\}|_\s*)*\s*[\)）]\s*)+$'
    return re.sub(pattern, '', text).strip()

def run_migration():
    print("=== 开始全量扫描与清洗数据库中选择题题干末尾残留的填空括号 ===")
    session = SessionLocal()

    cleaned_count = 0
    try:
        questions = session.query(Question).all()
        for q in questions:
            if q.question_type in ["single_choice", "multi_choice"] or (q.content and r"\begin{choices}" in q.content):
                content = q.content or ""
                if r"\begin{choices}" in content:
                    parts = content.split(r"\begin{choices}", 1)
                    cleaned_stem = clean_choice_stem_parentheses(parts[0])
                    if cleaned_stem != parts[0].strip():
                        q.content = cleaned_stem + "\n\\begin{choices}" + parts[1]
                        cleaned_count += 1
                else:
                    cleaned_content = clean_choice_stem_parentheses(content)
                    if cleaned_content != content:
                        q.content = cleaned_content
                        cleaned_count += 1
        
        session.commit()
        print(f"清洗完成！共升级并净化了 {cleaned_count} 道选择题题干中的残留末尾空括号。")
        
        # 同步导出备份文件
        print("正在同步更新 data_backup 备份文件...")
        export_database_to_files()
        print("备份文件同步更新完毕！")

    except Exception as e:
        session.rollback()
        print(f"迁移处理发生异常: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    run_migration()
