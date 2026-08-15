"""Pure helpers for conservative single-question type detection."""

import re


QUESTION_FORM_CHOICE = "choice"
QUESTION_FORM_FILL_IN_BLANK = "fill_in_blank"
QUESTION_FORM_DETAILED_ANSWER = "detailed_answer"
QUESTION_FORM_UNKNOWN = "unknown"


_CHOICES_ENV_PATTERN = re.compile(
    r"\\begin\s*\{\s*choices\s*\}",
    re.IGNORECASE,
)
_FILLIN_PATTERN = re.compile(r"\\fillin\b", re.IGNORECASE)


def detect_structured_question_form(content: str) -> str | None:
    """Return a definitive form only when normalized LaTeX structure proves it.

    A choices environment takes precedence because a choice option can itself
    contain a blank-like expression without changing the enclosing question
    into a fill-in-the-blank item.
    """

    normalized = str(content or "")
    if _CHOICES_ENV_PATTERN.search(normalized):
        return QUESTION_FORM_CHOICE
    if _FILLIN_PATTERN.search(normalized):
        return QUESTION_FORM_FILL_IN_BLANK
    return None


def normalize_ai_question_form(value: object) -> str:
    """Collapse model output to a coarse form that cannot encode single/multi."""

    normalized = str(value or "").strip().lower()
    mapping = {
        "choice": QUESTION_FORM_CHOICE,
        "choice_question": QUESTION_FORM_CHOICE,
        "single_choice": QUESTION_FORM_CHOICE,
        "multi_choice": QUESTION_FORM_CHOICE,
        "选择题": QUESTION_FORM_CHOICE,
        "单选题": QUESTION_FORM_CHOICE,
        "多选题": QUESTION_FORM_CHOICE,
        "fill_in_blank": QUESTION_FORM_FILL_IN_BLANK,
        "fill-in-blank": QUESTION_FORM_FILL_IN_BLANK,
        "填空题": QUESTION_FORM_FILL_IN_BLANK,
        "detailed_answer": QUESTION_FORM_DETAILED_ANSWER,
        "解答题": QUESTION_FORM_DETAILED_ANSWER,
        "unknown": QUESTION_FORM_UNKNOWN,
        "未知": QUESTION_FORM_UNKNOWN,
    }
    return mapping.get(normalized, QUESTION_FORM_UNKNOWN)
