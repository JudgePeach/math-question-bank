import os
import json
import time
import pytest
from database import Question
from sync_helper import clean_latex_to_markdown_for_ai, export_database_to_files
from main import clean_orphaned_images

def test_clean_latex_to_markdown_for_ai():
    # Test itemize list cleaning
    latex_text = "\\begin{itemize}\n\\item 第一项\n\\item[A.] 选项 A\n\\end{itemize}"
    cleaned = clean_latex_to_markdown_for_ai(latex_text)
    # Normalize whitespaces for robust assertion
    normalized = " ".join(cleaned.split())
    assert "- 第一项" in normalized
    assert "- A. 选项 A" in normalized
    assert "\\begin{itemize}" not in cleaned
    assert "\\end{itemize}" not in cleaned

    # Test underline/blank cleaning
    latex_underline = "请在 \\underline{\\hspace{2cm}} 处填空，或者使用 \\underline{自定义内容}"
    cleaned_ul = clean_latex_to_markdown_for_ai(latex_underline)
    assert "_______" in cleaned_ul
    assert "\\underline" not in cleaned_ul

    # Test double backslashes formatting
    latex_newline = "第一行\\\\第二项\\\\\\第三行"
    cleaned_nl = clean_latex_to_markdown_for_ai(latex_newline)
    assert "\n" in cleaned_nl
    assert "\\\\" not in cleaned_nl

    # Test formula preservation: Standard formulas inside $...$ or $$...$$ must be kept!
    latex_formula = "设函数 $f(x) = x^2 + 2x$ 在区间 $[0, 1]$ 上的最大值为 $$M$$"
    cleaned_formula = clean_latex_to_markdown_for_ai(latex_formula)
    assert "$f(x) = x^2 + 2x$" in cleaned_formula
    assert "$$M$$" in cleaned_formula


def test_export_database_to_files(db_session, tmp_path):
    # Setup mock Question
    q = Question(
        content="这只是一道测试同步导出的题目 $x+y=2$",
        question_type="single_choice",
        category_compulsory="必修一",
        category_chapter="第一章",
        category_knowledge="知识点A",
        difficulty="easy"
    )
    db_session.add(q)
    db_session.commit()

    # Re-route backup paths in sync_helper module to tmp_path to avoid writing to real data_backup directory during tests
    import sync_helper
    original_backup_dir = sync_helper.BACKUP_DIR
    original_json_path = sync_helper.JSON_BACKUP_PATH
    original_md_path = sync_helper.MD_BACKUP_PATH

    sync_helper.BACKUP_DIR = str(tmp_path)
    sync_helper.JSON_BACKUP_PATH = os.path.join(sync_helper.BACKUP_DIR, "questions_backup.json")
    sync_helper.MD_BACKUP_PATH = os.path.join(sync_helper.BACKUP_DIR, "questions_library.md")

    try:
        # Run export
        export_database_to_files(db=db_session)

        # Check JSON export
        assert os.path.exists(sync_helper.JSON_BACKUP_PATH)
        with open(sync_helper.JSON_BACKUP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert len(data) == 1
            assert data[0]["content"] == q.content

        # Check MD export
        assert os.path.exists(sync_helper.MD_BACKUP_PATH)
        with open(sync_helper.MD_BACKUP_PATH, "r", encoding="utf-8") as f:
            md_content = f.read()
            assert "这只是一道测试同步导出的题目" in md_content
            assert "$x+y=2$" in md_content
            assert "必修一" in md_content
    finally:
        # Restore paths
        sync_helper.BACKUP_DIR = original_backup_dir
        sync_helper.JSON_BACKUP_PATH = original_json_path
        sync_helper.MD_BACKUP_PATH = original_md_path


def test_clean_orphaned_images(db_session):
    import main
    upload_dir = main.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)

    # 1. Create a question referencing one image
    ref_image_path = f"{main.UPLOAD_DIR_REL}/test_referenced_old_image.png"
    q = Question(
        content="测试图片引用",
        question_type="single_choice"
    )
    q.image_paths = ["/" + ref_image_path] # standard absolute path starting with slash
    db_session.add(q)
    db_session.commit()

    # 2. Create the physical files
    ref_full_path = os.path.join(upload_dir, "test_referenced_old_image.png")
    old_orphan_path = os.path.join(upload_dir, "test_orphan_old_image.png")
    new_orphan_path = os.path.join(upload_dir, "test_orphan_new_image.png")

    for p in [ref_full_path, old_orphan_path, new_orphan_path]:
        with open(p, "w") as f:
            f.write("test_image_data")

    # Set file modification times (mtimes)
    now = time.time()
    two_hours_ago = now - 7200
    
    os.utime(ref_full_path, (two_hours_ago, two_hours_ago))     # Old but Referenced
    os.utime(old_orphan_path, (two_hours_ago, two_hours_ago))   # Old and Orphaned
    os.utime(new_orphan_path, (now, now))                       # New and Orphaned

    try:
        # Run clean_orphaned_images
        clean_orphaned_images()

        # Check outcomes based on three safety guardrails:
        # - Old and Referenced: MUST BE KEPT!
        assert os.path.exists(ref_full_path)

        # - Old and Orphaned: MUST BE DELETED!
        assert not os.path.exists(old_orphan_path)

        # - New and Orphaned: MUST BE KEPT (due to 1-hour safety grace period)!
        assert os.path.exists(new_orphan_path)

    finally:
        # Clean up remaining created files
        for p in [ref_full_path, old_orphan_path, new_orphan_path]:
            if os.path.exists(p):
                os.remove(p)
