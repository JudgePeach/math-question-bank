# AI Database Query Guide: Local Math Question Bank

> [!NOTE]
> **To any AI Agent (Claude, Cursor, Cline, etc.) reading this guide:**
> You are operating within a local workspace equipped with an SQLite-based High School Mathematics Question Bank (`math_question_bank.db`).
> Instead of reading raw files or loading massive markdown lists that overflow your context window, you should **proactively execute the local CLI script `search_questions.py`** in the terminal to search, retrieve, and format questions with 100% precision.

---

## 1. Quick Start: The CLI Search Tool (`search_questions.py`)

You can run `python3 search_questions.py` directly in the shell to fuzzy search questions. It automatically returns beautifully formatted Markdown with LaTeX formulas fully preserved.

### Parameter Reference
| Option | Long Option | Description | Example / Allowed Values |
| :--- | :--- | :--- | :--- |
| `-q` | `--query` | Fuzzy search keyword (matches grade, chapter, knowledge points, or content). | `-q "1.1"` or `-q "三角函数"` |
| `-n` | `--limit` | Maximum number of questions to return. **Use `-1` for NO LIMIT.** | `-n 50` or `-n -1` (default: 50) |
| `-a` | `--with-answers` | Flag to include answers, step-by-step explanations, and reviews. | (Omitting this hides answers) |
| `-t` | `--type` | Filter by question type. | `single_choice`, `multi_choice`, `fill_in_blank`, `detailed_answer` |
| `-d` | `--difficulty` | Filter by difficulty level. | `easy`, `medium`, `hard` |
| `-r` | `--related-to` | Fetch all questions linked to a specific Question ID. | `-r 3` |

---

## 2. Dynamic Search Examples (Copy & Execute)

### 📌 Case A: Generate Student Practice Sheet (No Answers)
To find all questions in **Compulsory 1, Chapter 1, Section 1 (1.1 集合的概念)** without leaking answers:
```bash
python3 search_questions.py -q "1.1" -n -1
```

### 📌 Case B: Generate Lesson Plan / Teacher Guide (With Answers)
To retrieve **3 difficult questions about Geometry** with detailed derivations and reviews:
```bash
python3 search_questions.py -q "立体几何" -d "hard" -n 3 -a
```

### 📌 Case C: Find Linked / Variation Questions
To grab all variations or linked sub-questions associated with a known question ID (e.g., ID `#3`):
```bash
python3 search_questions.py -r 3 -a
```

---

## 3. Core Database Table Schema (For Text-to-SQL / Custom Queries)

If you are a advanced Agent authorized to query the SQLite database (`math_question_bank.db`) directly using Python's `sqlite3` or SQLAlchemy, utilize this exact DDL structure of the `questions` table:

```sql
CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,                  -- Question stem (LaTeX + Markdown mixed)
    question_type VARCHAR(50),              -- Type: single_choice, multi_choice, fill_in_blank, detailed_answer
    category_compulsory VARCHAR(100),       -- Curriculum: "必修一", "必修二", "选择性必修一"
    category_chapter VARCHAR(100),          -- Chapter: e.g. "1. 集合与常用逻辑用语"
    category_knowledge VARCHAR(100),        -- Knowledge Point / Section: e.g. "1.1 集合的概念"
    difficulty VARCHAR(50),                 -- Difficulty: easy, medium, hard
    source VARCHAR(200),                    -- Source / Exam Origin: e.g. "2025 武汉二中高一月考"
    answer_markdown TEXT,                   -- Answers & Explanations (LaTeX + Markdown mixed)
    review TEXT,                            -- Teacher's review / comments (can be blank)
    association_group_id VARCHAR(100),      -- Bi-directional grouping token for associated variations
    image_paths TEXT,                       -- JSON string list of local relative image paths
    created_at DATETIME
);
```

---

## 4. Prompt Recipes for Users to Instruct AI

When you want your AI assistant to generate lesson plans, exam sheets, or slides, simply paste one of these prompts:

### 💬 Lesson Plan Generation Prompt
> "Please read [AI_DATABASE_GUIDE.md](./AI_DATABASE_GUIDE.md) first. Then, run the command `python3 search_questions.py -q "三角函数" -n 3 -a` in the terminal. Use the returned 3 mathematics questions with answers as classroom examples to draft a highly professional high-school lesson plan."

### 💬 Student Worksheet Generation Prompt
> "Read [AI_DATABASE_GUIDE.md](./AI_DATABASE_GUIDE.md). Run `python3 search_questions.py -q "1.1" -n -1` to fetch all questions for section 1.1. Select 5 of them to assemble a clean quiz sheet for students (do not include answers)."
