#!/usr/bin/env python3
"""
migrate_fillin.py - 历史题库填空题下划线批量升级为 \\fillin 宏工具
------------------------------------------------------------------
该脚本用于扫描本地 SQLite 数据库中所有已入库的题目，自动将题干中的旧下划线格式
（例如 ______、\\underline{...}、\\fillin[...]）一律规范化为最纯粹干净的 \\fillin 宏，
并在完成后自动同步更新 JSON 数据备份与 AI 专属只读题库文件。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, Question
from main import normalize_fillin_macro
from sync_helper import export_database_to_files

def migrate_database_fillin():
    print("=" * 65)
    print("🚀 [Fillin Migration] 开始扫描本地题库数据库并升级填空题下划线...")
    print("=" * 65)

    db = SessionLocal()
    try:
        questions = db.query(Question).all()
        total_questions = len(questions)
        migrated_count = 0

        for q in questions:
            if not q.content:
                continue
            
            old_content = q.content
            new_content = normalize_fillin_macro(old_content)

            if new_content != old_content:
                migrated_count += 1
                print(f"  [升级] 题目 #{q.id} ({q.question_type or '未知题型'}) 内容已更新:")
                print(f"    - 原格式: {old_content[:70]}...")
                print(f"    - 新格式: {new_content[:70]}...\n")
                q.content = new_content

        if migrated_count > 0:
            db.commit()
            print(f"✅ 成功升级 {migrated_count} / {total_questions} 道题目的下划线为纯净 \\fillin 宏！")
            
            print("\n🔄 正在同步导出最新数据至 JSON 备份文件与 AI 专属题库...")
            export_database_to_files()
            print("🎉 自动同步完成！(JSON 备份与 Markdown 题库已刷新)")
        else:
            print(f"✨ 数据库中所有 {total_questions} 道题目均已是纯净的 \\fillin 格式，无需重复迁移。")

        print("=" * 65)
    except Exception as e:
        db.rollback()
        print(f"❌ 迁移升级过程中发生异常: {str(e)}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    migrate_database_fillin()
