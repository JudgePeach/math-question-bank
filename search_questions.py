#!/usr/bin/env python3
import sqlite3
import argparse
import sys
import os

# Database Path
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_question_bank.db")

def get_association_group(question_id):
    """Finds the association group ID for a specific question ID."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT association_group_id FROM questions WHERE id = ?", (question_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] and row[0].strip():
        return row[0].strip()
    return None

def search_questions(query=None, qtype=None, difficulty=None, limit=50, with_answers=False, associated_to_id=None):
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Build SQL dynamically
    base_query = """
        SELECT id, content, question_type, category_compulsory, category_chapter, category_knowledge, difficulty, source, answer_markdown, review, association_group_id, tags
        FROM questions
        WHERE 1=1
    """
    params = []

    # Filter by association group if requested
    if associated_to_id:
        group_id = get_association_group(associated_to_id)
        if not group_id:
            conn.close()
            return []  # No association group found for this question
        base_query += " AND association_group_id = ? AND id != ?"
        params.extend([group_id, associated_to_id])

    # Fuzzy search query
    if query:
        base_query += """
            AND (
                category_compulsory LIKE ? OR
                category_chapter LIKE ? OR
                category_knowledge LIKE ? OR
                content LIKE ? OR
                source LIKE ? OR
                tags LIKE ?
            )
        """
        like_query = f"%{query}%"
        params.extend([like_query, like_query, like_query, like_query, like_query, like_query])

    if qtype:
        base_query += " AND question_type = ?"
        params.append(qtype)

    if difficulty:
        base_query += " AND difficulty = ?"
        params.append(difficulty)

    # Order by newest first
    base_query += " ORDER BY id DESC"
    
    # SQLite supports LIMIT -1 for "no limit"
    if limit is not None:
        base_query += " LIMIT ?"
        params.append(limit)

    cursor.execute(base_query, params)
    rows = cursor.fetchall()
    conn.close()

    return rows

def format_type(t):
    mapping = {
        "single_choice": "单选题",
        "multi_choice": "多选题",
        "fill_in_blank": "填空题",
        "detailed_answer": "解答题"
    }
    return mapping.get(t, t)

def format_difficulty(d):
    mapping = {
        "easy": "简单",
        "medium": "中等",
        "hard": "困难"
    }
    return mapping.get(d, d)

def main():
    parser = argparse.ArgumentParser(
        description="Fuzzy query tool to fetch mathematical questions from local SQLite DB for AI referencing."
    )
    parser.add_argument("-q", "--query", type=str, help="Search term (fuzzy matches compulsory, chapter, knowledge, content, etc.)")
    parser.add_argument("-t", "--type", type=str, choices=["single_choice", "multi_choice", "fill_in_blank", "detailed_answer"], help="Filter by question type")
    parser.add_argument("-d", "--difficulty", type=str, choices=["easy", "medium", "hard"], help="Filter by difficulty")
    parser.add_argument("-n", "--limit", type=int, default=50, help="Max number of questions to return. Use -1 for no limit (default: 50)")
    parser.add_argument("-a", "--with-answers", action="store_true", help="Include answers and explanations in the output")
    parser.add_argument("-r", "--related-to", type=int, help="Fetch all questions associated/related to the given Question ID")

    args = parser.parse_args()

    # If user wants all questions, they can set -n to -1
    limit_val = args.limit

    results = search_questions(
        query=args.query,
        qtype=args.type,
        difficulty=args.difficulty,
        limit=limit_val,
        with_answers=args.with_answers,
        associated_to_id=args.related_to
    )

    if not results:
        if args.related_to:
            print(f"### No associated/related questions found for ID: #{args.related_to} (or it does not belong to any association group)")
        else:
            print(f"### No questions found matching query: '{args.query or 'All'}'")
        return

    title_msg = f"## 关联题目检索结果 (与 ID: #{args.related_to} 关联的题目)" if args.related_to else f"## 题库检索结果 (共找到 {len(results)} 道符合条件的题目)"
    print(f"{title_msg}\n")
    
    for idx, row in enumerate(results, 1):
        q_id, content, question_type, compulsory, chapter, knowledge, difficulty, source, answer, review, group_id, tags = row
        
        print(f"### 题目 {idx} (ID: #{q_id})")
        print(f"- **分类学段**: `{compulsory or '未分类'}`")
        print(f"- **章节知识点**: `{chapter or '无'}` -> `{knowledge or '无'}`")
        print(f"- **题型/难度**: {format_type(question_type)} | {format_difficulty(difficulty)}")
        if source:
            print(f"- **来源**: *{source}*")
        if group_id:
            print(f"- **关联组 ID**: `{group_id}`")
        if tags:
            print(f"- **自定义标签**: *{tags}*")
        
        print(f"\n#### 【题干】\n{content}\n")
        
        if args.with_answers:
            if answer:
                print(f"#### 【答案与解析】\n{answer}\n")
            if review:
                print(f"#### 【教师点评】\n{review}\n")
        else:
            print(f"> [!NOTE]\n> *答案与详细解析已隐藏。如需查看，请在查询命令中添加 `-a` 或 `--with-answers` 参数。*\n")
            
        print("---\n")

if __name__ == "__main__":
    main()
