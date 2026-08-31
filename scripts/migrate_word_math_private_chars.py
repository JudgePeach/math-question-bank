#!/usr/bin/env python3
"""Repair verified Word math Private Use Area glyphs in stored questions."""

import sys

from mathbank.database import Question, QuestionFingerprint, SessionLocal
from mathbank.omml_helper import (
    find_unknown_word_math_private_characters,
    normalize_known_word_math_private_characters,
    normalize_word_linear_latex_boundaries,
)
from mathbank.sync_helper import export_database_to_files


QUESTION_TEXT_FIELDS = ("content", "answer_markdown", "review")


def migrate_database_word_math_private_chars() -> dict:
    """Replace verified PUA glyphs and report unknown ones without guessing."""
    db = SessionLocal()
    changed_questions = []
    changed_fields = 0
    unknown_by_question = {}
    try:
        questions = db.query(Question).order_by(Question.id.asc()).all()
        for question in questions:
            question_changed = False
            unknown_labels = set()
            for field in QUESTION_TEXT_FIELDS:
                old_value = getattr(question, field, "") or ""
                unknown_labels.update(find_unknown_word_math_private_characters(old_value))
                new_value = normalize_word_linear_latex_boundaries(
                    normalize_known_word_math_private_characters(old_value)
                )
                if new_value != old_value:
                    setattr(question, field, new_value)
                    changed_fields += 1
                    question_changed = True
            if question_changed:
                changed_questions.append(question.id)
            if unknown_labels:
                unknown_by_question[question.id] = sorted(unknown_labels)

        if changed_questions:
            db.query(QuestionFingerprint).filter(
                QuestionFingerprint.question_id.in_(changed_questions)
            ).delete(synchronize_session=False)
            db.commit()
            export_database_to_files(db)
        else:
            db.rollback()

        result = {
            "scanned": len(questions),
            "changed_questions": changed_questions,
            "changed_fields": changed_fields,
            "unknown_by_question": unknown_by_question,
        }
        print(
            f"[Word Math PUA] 扫描 {result['scanned']} 道题，"
            f"修复 {len(changed_questions)} 道题的 {changed_fields} 个字段。"
        )
        if changed_questions:
            print("[Word Math PUA] 已修复题目 ID：" + ", ".join(map(str, changed_questions)))
        if unknown_by_question:
            print("[Word Math PUA] 仍有未知私用字符，未进行猜测替换：")
            for question_id, labels in unknown_by_question.items():
                print(f"  - 题目 #{question_id}: {', '.join(labels)}")
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    try:
        migrate_database_word_math_private_chars()
    except Exception as exc:
        print(f"[Word Math PUA] 迁移失败：{exc}")
        sys.exit(1)
